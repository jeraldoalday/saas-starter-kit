"""Track, rotate and revoke the refresh tokens that keep people signed in.

The refresh token is still a JWT — issued, signed and decoded by
`app.core.security` exactly as before. What this module adds is the row that
makes it *stoppable*: `authenticate` requires a matching, unrevoked,
unexpired `auth_sessions` record, so ending a session is an UPDATE rather
than a rotation of SECRET_KEY that would sign out every account at once.

Two properties are worth stating outright, because the rest of the module
follows from them:

1. **Rotation.** Every refresh mints a new token and overwrites the stored
   digest in place. The previous token is retired by that overwrite — its
   digest is simply no longer in the table — so a token captured in a log or
   a proxy stops working the next time the real client refreshes, instead of
   staying valid for the remaining weeks of its lifetime.

2. **Revocation is a tombstone.** A revoked session keeps its row, the way a
   revoked API key does, so "when did this device get signed out?" is still
   answerable afterwards.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_token
from app.core.timeutils import as_utc
from app.models.auth_session import (
    MAX_IP_LENGTH,
    MAX_USER_AGENT_LENGTH,
    AuthSession,
)
from app.models.user import User
from app.services.api_key_service import hash_key

logger = logging.getLogger("app.sessions")

# A ceiling per account. Not a security boundary — it is a bound on how far
# one account's session list can grow when a client re-logs in instead of
# refreshing. Reaching it retires the oldest session rather than refusing
# the login: failing a correct password because of housekeeping would be a
# worse bug than the unbounded table it prevents.
MAX_ACTIVE_SESSIONS_PER_USER = 10


class SessionNotFoundError(Exception):
    """No session with that id belongs to this account."""


def hash_refresh_token(raw_token: str) -> str:
    """Digest a refresh token for storage or lookup.

    Shares api_key_service.hash_key rather than restating it: same algorithm,
    same reasoning (a high-entropy value needs a fast digest, not a KDF), and
    one implementation means one place to change if that ever stops holding.
    """
    return hash_key(raw_token)


def _token_expiry(raw_token: str, *, now: datetime) -> datetime:
    """When the row should stop working, taken from the token itself.

    Read from the JWT's own `exp` so the row and the token cannot disagree.
    A token without a usable claim falls back to the configured lifetime —
    that path is unreachable for tokens this application mints, and guessing
    high would be the dangerous direction to guess in.
    """
    payload = decode_token(raw_token)
    expiry = payload.get("exp") if payload else None
    if expiry is not None:
        try:
            return datetime.fromtimestamp(int(expiry), tz=UTC)
        except (OverflowError, OSError, TypeError, ValueError):
            logger.warning("refresh token carried an unreadable exp claim")

    return now + timedelta(days=get_settings().refresh_token_expire_days)


def _truncate(value: str | None, limit: int) -> str | None:
    """Clip client-supplied context to what the column holds.

    Truncated rather than rejected: a User-Agent is decoration for the
    session list, and a header nobody validates must not be able to fail a
    login.
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped[:limit]


def start_session(
    db: Session,
    user: User,
    raw_token: str,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
    now: datetime | None = None,
) -> AuthSession:
    """Record a new signed-in device holding `raw_token`."""
    now = now or datetime.now(UTC)

    session = AuthSession(
        user_id=user.id,
        token_hash=hash_refresh_token(raw_token),
        created_at=now,
        last_used_at=now,
        expires_at=_token_expiry(raw_token, now=now),
        user_agent=_truncate(user_agent, MAX_USER_AGENT_LENGTH),
        ip_address=_truncate(ip_address, MAX_IP_LENGTH),
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    _enforce_session_cap(db, user, now=now)

    logger.info(
        "session started",
        extra={"session_id": str(session.id), "user_id": str(user.id)},
    )
    return session


def _enforce_session_cap(db: Session, user: User, *, now: datetime) -> None:
    """Retire the oldest sessions past MAX_ACTIVE_SESSIONS_PER_USER."""
    active = [s for s in _sessions_for(db, user) if s.is_usable(now)]
    excess = len(active) - MAX_ACTIVE_SESSIONS_PER_USER
    if excess <= 0:
        return

    # _sessions_for is newest-first, so the tail is the oldest.
    for stale in active[-excess:]:
        stale.revoked_at = now
    db.commit()
    logger.info(
        "retired sessions over the per-account cap",
        extra={"user_id": str(user.id), "retired": excess},
    )


def _sessions_for(db: Session, user: User) -> list[AuthSession]:
    stmt = (
        select(AuthSession)
        .where(AuthSession.user_id == user.id)
        .order_by(AuthSession.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def list_sessions(db: Session, user: User) -> list[AuthSession]:
    """This account's sessions, newest first, revoked ones included.

    Revoked rows stay in the list for the same reason revoked API keys do:
    after an incident, "that device was signed out on the 3rd" is the answer
    someone is looking for.
    """
    return _sessions_for(db, user)


def authenticate(
    db: Session, raw_token: str, *, now: datetime | None = None
) -> AuthSession | None:
    """Resolve a raw refresh token to its live session, or None.

    None covers every failure identically — unknown, rotated away, revoked,
    expired, or belonging to a deactivated account — because the caller can
    act on all of them the same way, and distinguishing them would help
    someone probing with stolen tokens.
    """
    now = now or datetime.now(UTC)
    stmt = select(AuthSession).where(
        AuthSession.token_hash == hash_refresh_token(raw_token)
    )
    session = db.execute(stmt).scalar_one_or_none()

    if session is None or not session.is_usable(now):
        return None

    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        return None

    return session


def rotate(
    db: Session,
    session: AuthSession,
    new_raw_token: str,
    *,
    now: datetime | None = None,
) -> AuthSession:
    """Swap in a freshly minted token, retiring the one just used.

    Overwriting the digest is what retires the old token: nothing else needs
    deleting, because a lookup of the previous value now finds no row. That
    also means a stolen token stops working as soon as the legitimate client
    refreshes — the theft window closes on its own.
    """
    now = now or datetime.now(UTC)

    session.token_hash = hash_refresh_token(new_raw_token)
    session.last_used_at = now
    session.expires_at = _token_expiry(new_raw_token, now=now)
    db.commit()
    db.refresh(session)
    return session


def revoke_session(
    db: Session, user: User, session_id: uuid.UUID, *, now: datetime | None = None
) -> AuthSession:
    """End one of this account's sessions.

    Idempotent: revoking an already-revoked session keeps the original
    timestamp, because the interesting moment is when it stopped working.
    """
    stmt = select(AuthSession).where(
        AuthSession.id == session_id, AuthSession.user_id == user.id
    )
    session = db.execute(stmt).scalar_one_or_none()
    if session is None:
        # 404 rather than 403 upstream: whether a session id exists on
        # someone else's account is not this caller's business.
        raise SessionNotFoundError(str(session_id))

    if session.revoked_at is None:
        session.revoked_at = now or datetime.now(UTC)
        db.commit()
        db.refresh(session)
        logger.info("session revoked", extra={"session_id": str(session.id)})

    return session


def revoke_all(
    db: Session,
    user: User,
    *,
    except_session_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> int:
    """End every session this account holds; returns how many were ended.

    `except_session_id` is what makes "sign out my *other* devices" possible
    without the caller signing themselves out in the process. Password reset
    passes nothing, because there the whole point is that every outstanding
    token dies — including one held by whoever forced the reset.
    """
    now = now or datetime.now(UTC)
    revoked = 0
    for session in _sessions_for(db, user):
        if session.id == except_session_id or not session.is_usable(now):
            continue
        session.revoked_at = now
        revoked += 1

    if revoked:
        db.commit()
        logger.info(
            "sessions revoked in bulk",
            extra={"user_id": str(user.id), "revoked": revoked},
        )

    return revoked


def count_active_sessions(
    db: Session, user: User, *, now: datetime | None = None
) -> int:
    """How many of this account's sessions could refresh right now.

    Counted in Python rather than SQL because "usable" folds in expiry,
    which the model already knows how to compare (including the
    naive-datetime handling SQLite forces).
    """
    now = now or datetime.now(UTC)
    return sum(1 for session in _sessions_for(db, user) if session.is_usable(now))


def purge_expired_sessions(
    db: Session, *, older_than_days: int, now: datetime | None = None
) -> int:
    """Delete sessions dead longer than the retention window.

    A session row is worth keeping for a while after it stops working — it
    is what answers "when was this device signed out?" — but not forever.
    Both ways a session dies count from the moment it died: revoked rows
    from `revoked_at`, expired ones from `expires_at`.
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=max(older_than_days, 1))

    stale = [
        session
        for session in db.execute(select(AuthSession)).scalars().all()
        if _died_before(session, cutoff)
    ]
    for session in stale:
        db.delete(session)
    db.commit()

    logger.info("pruned auth sessions", extra={"deleted": len(stale)})
    return len(stale)


def _died_before(session: AuthSession, cutoff: datetime) -> bool:
    """Whether this session stopped working before `cutoff`.

    A revoked session's clock starts at revocation, which can be long before
    the token would have expired on its own; an untouched one's starts at
    expiry.
    """
    revoked_at = as_utc(session.revoked_at)
    if revoked_at is not None:
        return revoked_at < cutoff

    expires_at = as_utc(session.expires_at)
    return expires_at is not None and expires_at < cutoff


def count_sessions(db: Session) -> int:
    """Total sessions across all accounts — for the admin overview."""
    return int(db.execute(select(func.count()).select_from(AuthSession)).scalar_one())


def count_all_active_sessions(db: Session, *, now: datetime | None = None) -> int:
    """Live sessions across every account, counted in the database.

    The per-account counterpart, `count_active_sessions`, is deliberately
    counted in Python: it folds in expiry, and the model already knows how
    to compare that. That is a fine trade over one account's ten sessions
    and a bad one over a deployment's, where the alternative is loading
    every row in the table to add up a single integer.

    So liveness is restated as a WHERE clause here. It has to agree with
    `AuthSession.is_usable`, and does: unrevoked, and not yet expired.

    The cutoff is normalised to UTC before it is bound. SQLite stores naive
    wall-clock UTC and its driver ignores tzinfo entirely, so an aware
    datetime in any other zone would be compared as though its local
    wall-clock reading were UTC — off by the offset, silently, and only on
    the dialect the tests run on.
    """
    now = as_utc(now) or datetime.now(UTC)
    stmt = (
        select(func.count())
        .select_from(AuthSession)
        .where(AuthSession.revoked_at.is_(None), AuthSession.expires_at > now)
    )
    return int(db.execute(stmt).scalar_one())


__all__ = [
    "MAX_ACTIVE_SESSIONS_PER_USER",
    "SessionNotFoundError",
    "authenticate",
    "count_active_sessions",
    "count_all_active_sessions",
    "count_sessions",
    "hash_refresh_token",
    "list_sessions",
    "purge_expired_sessions",
    "revoke_all",
    "revoke_session",
    "rotate",
    "start_session",
]

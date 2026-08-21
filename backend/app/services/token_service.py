"""Issue and redeem single-use email tokens."""

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.token import OneTimeToken, TokenPurpose
from app.models.user import User

logger = logging.getLogger("app.tokens")

# 32 bytes of urandom, url-safe encoded: long enough that guessing is
# infeasible and safe to put in a query string.
TOKEN_BYTES = 32

VERIFICATION_TTL = timedelta(hours=24)
PASSWORD_RESET_TTL = timedelta(hours=1)


def hash_token(raw_token: str) -> str:
    """SHA-256 of the raw token.

    Plain SHA-256 rather than bcrypt is deliberate: these tokens are already
    high-entropy random values, so there is no weak secret to slow down
    guessing of, and lookup needs to be a single indexed query.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def issue_token(
    db: Session, user: User, purpose: TokenPurpose, ttl: timedelta | None = None
) -> str:
    """Create a token for `user`, invalidating any earlier one of that purpose.

    Returns the raw token, which is never persisted - only its hash is.
    """
    _invalidate_outstanding(db, user, purpose)

    raw_token = secrets.token_urlsafe(TOKEN_BYTES)
    if ttl is None:
        ttl = (
            PASSWORD_RESET_TTL
            if purpose is TokenPurpose.PASSWORD_RESET
            else VERIFICATION_TTL
        )

    db.add(
        OneTimeToken(
            token_hash=hash_token(raw_token),
            user_id=user.id,
            purpose=purpose.value,
            expires_at=datetime.now(UTC) + ttl,
        )
    )
    db.commit()
    return raw_token


def _invalidate_outstanding(db: Session, user: User, purpose: TokenPurpose) -> None:
    """Mark prior unused tokens used, so only the newest link works."""
    outstanding = (
        db.query(OneTimeToken)
        .filter(
            OneTimeToken.user_id == user.id,
            OneTimeToken.purpose == purpose.value,
            OneTimeToken.used_at.is_(None),
        )
        .all()
    )
    now = datetime.now(UTC)
    for token in outstanding:
        token.used_at = now


def consume_token(
    db: Session, raw_token: str, purpose: TokenPurpose
) -> OneTimeToken | None:
    """Redeem a token, returning it only if it was valid and unused.

    The token is marked used before returning, so a replayed link fails even
    if the caller's own work later errors out.
    """
    token = (
        db.query(OneTimeToken)
        .filter(
            OneTimeToken.token_hash == hash_token(raw_token),
            OneTimeToken.purpose == purpose.value,
        )
        .first()
    )

    if token is None or not token.is_usable():
        return None

    token.used_at = datetime.now(UTC)
    db.commit()
    return token


def purge_expired(db: Session, *, now: datetime | None = None) -> int:
    """Delete tokens that can never be redeemed again. Returns rows removed.

    Two disjoint reasons a row is dead, and both have to be covered: it
    expired, or it was spent. Filtering on `expires_at` alone — which this
    did until it was wired into the retention job and someone counted the
    rows — leaves every consumed token sitting there until its TTL lapses.
    A used verification token therefore outlived its usefulness by 24 hours
    and a spent reset link by an hour, which is harmless for one account and
    is a steadily growing table across all of them.

    Deleting a spent token cannot resurrect the link: `consume_token`
    rejects on `used_at` before it ever looks at the row's age, so a replay
    of a deleted token misses on the hash lookup instead and fails the same
    way.
    """
    now = now or datetime.now(UTC)
    deleted = (
        db.query(OneTimeToken)
        .filter(
            or_(
                OneTimeToken.expires_at < now,
                OneTimeToken.used_at.is_not(None),
            )
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info("pruned one-time tokens", extra={"deleted": deleted})
    return int(deleted)

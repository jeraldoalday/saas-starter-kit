"""Issue and redeem single-use email tokens."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.token import OneTimeToken, TokenPurpose
from app.models.user import User

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


def purge_expired(db: Session) -> int:
    """Delete spent and expired tokens. Returns how many rows went."""
    deleted = (
        db.query(OneTimeToken)
        .filter(OneTimeToken.expires_at < datetime.now(UTC))
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted

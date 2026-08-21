"""Issue, verify and revoke API keys.

The shape of the credential is the whole design here:

    sk_live_Q1d6t8fZ...   ->  prefix "sk_live_Q1d6"  +  SHA-256 digest

The raw value exists only in the response that created it. Everything
afterwards works from the digest (verification, revocation) or the prefix
(showing a human which key is which). That means a leaked database gives an
attacker a list of digests and no way to call the API with them, and it
means "show me my keys" never needs the secrets back.
"""

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.timeutils import as_utc
from app.models.api_key import PREFIX_LENGTH, ApiKey
from app.models.user import User

logger = logging.getLogger("app.api_keys")

# Recognisable at a glance in a log or a config file, so a key pasted into
# the wrong field can be spotted (and scanned for) as a credential.
KEY_PREFIX = "sk_live_"

# 32 bytes from `secrets` — 256 bits of entropy, which is why the digest
# below can be a plain hash rather than a KDF.
SECRET_BYTES = 32

# A ceiling per account, so a runaway script can't turn key creation into an
# unbounded write. Well above what a real integration needs.
MAX_ACTIVE_KEYS_PER_USER = 20

# Longest lifetime a caller may request. A key that outlives the person who
# created it is the failure mode this bounds.
MAX_EXPIRY_DAYS = 365

# last_used_at is written at most this often per key. Without it every
# authenticated request would turn a read into a write, which is a lot of
# row churn to answer a question ("is this key still in use?") that nobody
# asks at minute resolution.
LAST_USED_RESOLUTION = timedelta(minutes=5)


class ApiKeyLimitExceededError(Exception):
    """The account already holds MAX_ACTIVE_KEYS_PER_USER usable keys."""


class ApiKeyNotFoundError(Exception):
    """No key with that id belongs to this account."""


def hash_key(raw_key: str) -> str:
    """Digest a raw key for storage or lookup.

    Lookup happens by digest equality in SQL, which is not constant time —
    acceptable because the digest is not a secret an attacker can work
    backwards from, and the alternative (scanning every row and comparing in
    Python) is both slower and no more private.
    """
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """Mint a new secret, returning (raw_key, prefix, digest)."""
    raw_key = f"{KEY_PREFIX}{secrets.token_urlsafe(SECRET_BYTES)}"
    return raw_key, raw_key[:PREFIX_LENGTH], hash_key(raw_key)


def active_key_count(db: Session, user: User, *, now: datetime | None = None) -> int:
    """How many of this account's keys could authenticate a request today.

    Counted in Python rather than SQL because "usable" folds in expiry,
    which is a comparison against now that the model already knows how to
    make (including the naive-datetime handling SQLite forces).
    """
    now = now or datetime.now(UTC)
    return sum(1 for key in _keys_for(db, user) if key.is_usable(now))


def _keys_for(db: Session, user: User) -> list[ApiKey]:
    stmt = (
        select(ApiKey)
        .where(ApiKey.user_id == user.id)
        .order_by(ApiKey.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def list_api_keys(db: Session, user: User) -> list[ApiKey]:
    """This account's keys, newest first, revoked ones included.

    Revoked keys stay in the list on purpose: "this key was revoked on the
    3rd" is the answer someone is looking for after an incident.
    """
    return _keys_for(db, user)


def create_api_key(
    db: Session,
    user: User,
    *,
    name: str,
    expires_in_days: int | None = None,
    now: datetime | None = None,
) -> tuple[ApiKey, str]:
    """Mint a key for `user`, returning the row and the raw secret.

    The raw secret is returned rather than stored, and this is the only
    moment it exists — the caller must hand it to the user in the same
    response or it is gone.
    """
    now = now or datetime.now(UTC)

    if active_key_count(db, user, now=now) >= MAX_ACTIVE_KEYS_PER_USER:
        raise ApiKeyLimitExceededError(
            f"At most {MAX_ACTIVE_KEYS_PER_USER} active keys per account"
        )

    expires_at: datetime | None = None
    if expires_in_days is not None:
        expires_at = now + timedelta(days=min(expires_in_days, MAX_EXPIRY_DAYS))

    raw_key, prefix, digest = generate_key()
    api_key = ApiKey(
        user_id=user.id,
        name=name.strip(),
        prefix=prefix,
        token_hash=digest,
        created_at=now,
        expires_at=expires_at,
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)

    logger.info(
        "api key created",
        extra={"api_key_id": str(api_key.id), "api_key_prefix": prefix},
    )
    return api_key, raw_key


def revoke_api_key(
    db: Session, user: User, key_id: uuid.UUID, *, now: datetime | None = None
) -> ApiKey:
    """Revoke one of this account's keys.

    Idempotent: revoking an already-revoked key keeps the original
    timestamp, because the interesting moment is when it *stopped* working,
    not when someone last clicked the button.
    """
    stmt = select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user.id)
    api_key = db.execute(stmt).scalar_one_or_none()
    if api_key is None:
        # 404 rather than 403 upstream: whether a key id exists on someone
        # else's account is not this caller's business.
        raise ApiKeyNotFoundError(str(key_id))

    if api_key.revoked_at is None:
        api_key.revoked_at = now or datetime.now(UTC)
        db.commit()
        db.refresh(api_key)
        logger.info(
            "api key revoked",
            extra={"api_key_id": str(api_key.id), "api_key_prefix": api_key.prefix},
        )

    return api_key


def authenticate_api_key(
    db: Session, raw_key: str, *, now: datetime | None = None
) -> User | None:
    """Resolve a raw key to its owner, or None.

    None covers every failure the caller can act on identically: malformed,
    unknown, expired, revoked, or owned by a deactivated account. Which one
    it was is deliberately not reported back — that distinction only helps
    someone probing for valid keys.
    """
    if not raw_key or not raw_key.startswith(KEY_PREFIX):
        # Cheap reject: a garbage Authorization header shouldn't cost a
        # database round trip.
        return None

    now = now or datetime.now(UTC)
    stmt = select(ApiKey).where(ApiKey.token_hash == hash_key(raw_key))
    api_key = db.execute(stmt).scalar_one_or_none()

    if api_key is None or not api_key.is_usable(now):
        return None

    user = db.get(User, api_key.user_id)
    if user is None or not user.is_active:
        return None

    _touch_last_used(db, api_key, now)
    return user


def _touch_last_used(db: Session, api_key: ApiKey, now: datetime) -> None:
    """Record use, at most once per LAST_USED_RESOLUTION.

    Best-effort like analytics: a failed bookkeeping write is not a reason
    to reject a request that authenticated correctly.
    """
    last_used = as_utc(api_key.last_used_at)
    if last_used is not None and now - last_used < LAST_USED_RESOLUTION:
        return

    try:
        api_key.last_used_at = now
        db.commit()
    except SQLAlchemyError:
        logger.exception("failed to record api key use")
        db.rollback()


def purge_revoked_keys(
    db: Session, *, older_than_days: int, now: datetime | None = None
) -> int:
    """Delete revocation tombstones past their usefulness; returns rows removed.

    The audit value of "revoked on the 3rd" decays; the row does not. Run
    this alongside the usage-event prune.
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=max(older_than_days, 1))
    deleted = (
        db.query(ApiKey)
        .filter(ApiKey.revoked_at.is_not(None), ApiKey.revoked_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info("pruned revoked api keys", extra={"deleted": deleted})
    return int(deleted)


def count_keys(db: Session) -> int:
    """Total keys across all accounts — for the admin overview."""
    return int(db.execute(select(func.count()).select_from(ApiKey)).scalar_one())


__all__ = [
    "ApiKeyLimitExceededError",
    "ApiKeyNotFoundError",
    "KEY_PREFIX",
    "MAX_ACTIVE_KEYS_PER_USER",
    "MAX_EXPIRY_DAYS",
    "active_key_count",
    "authenticate_api_key",
    "count_keys",
    "create_api_key",
    "generate_key",
    "hash_key",
    "list_api_keys",
    "purge_revoked_keys",
    "revoke_api_key",
]

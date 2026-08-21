"""Unit-level cover for key minting, verification and revocation.

The endpoint tests (tests/test_api_keys.py) exercise the HTTP surface; this
file stays at the service so the security-relevant behaviour — what is
stored, what stops working, and when — is asserted directly rather than
inferred from a status code.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import hash_password
from app.models.api_key import ApiKey
from app.models.user import User
from app.services.api_key_service import (
    KEY_PREFIX,
    LAST_USED_RESOLUTION,
    MAX_ACTIVE_KEYS_PER_USER,
    MAX_EXPIRY_DAYS,
    ApiKeyLimitExceededError,
    ApiKeyNotFoundError,
    active_key_count,
    authenticate_api_key,
    create_api_key,
    generate_key,
    hash_key,
    list_api_keys,
    purge_revoked_keys,
    revoke_api_key,
)


@pytest.fixture
def user(db_session) -> User:
    account = User(
        email="keys@example.com",
        hashed_password=hash_password("s3cret-password"),
        full_name="Key Holder",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


@pytest.fixture
def other_user(db_session) -> User:
    account = User(
        email="other@example.com", hashed_password=hash_password("s3cret-password")
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def test_generated_keys_are_prefixed_and_unique():
    first, prefix, digest = generate_key()
    second, _, _ = generate_key()

    assert first.startswith(KEY_PREFIX)
    assert first != second
    assert first.startswith(prefix)
    assert digest == hash_key(first)


def test_creation_stores_only_the_digest(db_session, user):
    api_key, raw_key = create_api_key(db_session, user, name="CI deploy")

    assert api_key.token_hash == hash_key(raw_key)
    assert raw_key not in (api_key.token_hash, api_key.prefix)
    # The prefix is a display aid, so it must be a recognisable slice of the
    # secret and nothing more than that.
    assert raw_key.startswith(api_key.prefix)
    assert len(api_key.prefix) < len(raw_key)


def test_creation_without_an_expiry_never_expires(db_session, user):
    api_key, _ = create_api_key(db_session, user, name="Forever")

    assert api_key.expires_at is None
    assert api_key.is_usable()


def test_requested_expiry_is_clamped_to_the_maximum(db_session, user):
    now = datetime(2026, 1, 1, tzinfo=UTC)

    api_key, _ = create_api_key(
        db_session, user, name="Contractor", expires_in_days=10_000, now=now
    )

    # SQLite gives the timestamp back naive, so compare in UTC explicitly.
    assert api_key.expires_at.replace(tzinfo=UTC) == now + timedelta(days=MAX_EXPIRY_DAYS)


def test_authentication_resolves_the_owner(db_session, user):
    _, raw_key = create_api_key(db_session, user, name="CI deploy")

    assert authenticate_api_key(db_session, raw_key).id == user.id


def test_authentication_rejects_unknown_and_malformed_keys(db_session, user):
    create_api_key(db_session, user, name="CI deploy")

    assert authenticate_api_key(db_session, "") is None
    assert authenticate_api_key(db_session, "not-a-key") is None
    assert authenticate_api_key(db_session, f"{KEY_PREFIX}wrong-secret") is None


def test_authentication_rejects_a_revoked_key(db_session, user):
    api_key, raw_key = create_api_key(db_session, user, name="Leaked")

    revoke_api_key(db_session, user, api_key.id)

    assert authenticate_api_key(db_session, raw_key) is None


def test_authentication_rejects_an_expired_key(db_session, user):
    now = datetime.now(UTC)
    _, raw_key = create_api_key(
        db_session, user, name="Short lived", expires_in_days=1, now=now
    )

    assert authenticate_api_key(db_session, raw_key, now=now) is not None
    assert authenticate_api_key(db_session, raw_key, now=now + timedelta(days=2)) is None


def test_authentication_rejects_keys_of_deactivated_accounts(db_session, user):
    _, raw_key = create_api_key(db_session, user, name="CI deploy")

    user.is_active = False
    db_session.commit()

    assert authenticate_api_key(db_session, raw_key) is None


def test_use_is_recorded_but_not_on_every_call(db_session, user):
    start = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    api_key, raw_key = create_api_key(db_session, user, name="Busy", now=start)

    authenticate_api_key(db_session, raw_key, now=start)
    first_seen = api_key.last_used_at
    assert first_seen is not None

    # A second call inside the resolution window leaves the row alone.
    authenticate_api_key(db_session, raw_key, now=start + timedelta(seconds=30))
    assert api_key.last_used_at == first_seen

    later = start + LAST_USED_RESOLUTION + timedelta(minutes=1)
    authenticate_api_key(db_session, raw_key, now=later)
    assert api_key.last_used_at != first_seen


def test_revocation_is_idempotent(db_session, user):
    api_key, _ = create_api_key(db_session, user, name="Leaked")

    first = revoke_api_key(db_session, user, api_key.id).revoked_at
    second = revoke_api_key(db_session, user, api_key.id).revoked_at

    assert first is not None
    assert first == second


def test_revocation_refuses_another_accounts_key(db_session, user, other_user):
    api_key, raw_key = create_api_key(db_session, user, name="Mine")

    with pytest.raises(ApiKeyNotFoundError):
        revoke_api_key(db_session, other_user, api_key.id)

    assert authenticate_api_key(db_session, raw_key) is not None


def test_revocation_reports_a_missing_key(db_session, user):
    with pytest.raises(ApiKeyNotFoundError):
        revoke_api_key(db_session, user, uuid.uuid4())


def test_listing_is_scoped_to_the_account_and_keeps_revoked_keys(
    db_session, user, other_user
):
    kept, _ = create_api_key(db_session, user, name="Kept")
    gone, _ = create_api_key(db_session, user, name="Gone")
    create_api_key(db_session, other_user, name="Theirs")
    revoke_api_key(db_session, user, gone.id)

    listed = list_api_keys(db_session, user)

    assert {key.id for key in listed} == {kept.id, gone.id}


def test_the_active_count_ignores_revoked_and_expired_keys(db_session, user):
    now = datetime.now(UTC)
    create_api_key(db_session, user, name="Live", now=now)
    revoked, _ = create_api_key(db_session, user, name="Revoked", now=now)
    create_api_key(db_session, user, name="Expiring", expires_in_days=1, now=now)
    revoke_api_key(db_session, user, revoked.id, now=now)

    assert active_key_count(db_session, user, now=now) == 2
    assert active_key_count(db_session, user, now=now + timedelta(days=2)) == 1


def test_creation_stops_at_the_per_account_ceiling(db_session, user):
    for index in range(MAX_ACTIVE_KEYS_PER_USER):
        create_api_key(db_session, user, name=f"Key {index}")

    with pytest.raises(ApiKeyLimitExceededError):
        create_api_key(db_session, user, name="One too many")


def test_revoking_frees_a_slot_under_the_ceiling(db_session, user):
    keys = [
        create_api_key(db_session, user, name=f"Key {index}")[0]
        for index in range(MAX_ACTIVE_KEYS_PER_USER)
    ]

    revoke_api_key(db_session, user, keys[0].id)

    # Revoked keys are still rows, so this also proves the ceiling counts
    # usable keys rather than everything ever issued.
    assert create_api_key(db_session, user, name="Replacement")[0] is not None


def test_pruning_removes_only_old_tombstones(db_session, user):
    now = datetime.now(UTC)
    live, _ = create_api_key(db_session, user, name="Live")
    recent, _ = create_api_key(db_session, user, name="Recently revoked")
    ancient, _ = create_api_key(db_session, user, name="Long revoked")
    revoke_api_key(db_session, user, recent.id, now=now - timedelta(days=1))
    revoke_api_key(db_session, user, ancient.id, now=now - timedelta(days=400))

    removed = purge_revoked_keys(db_session, older_than_days=90, now=now)

    remaining = {key.id for key in list_api_keys(db_session, user)}
    assert removed == 1
    assert remaining == {live.id, recent.id}


def test_deleting_an_account_takes_its_keys(db_session, user):
    create_api_key(db_session, user, name="CI deploy")

    db_session.delete(user)
    db_session.commit()

    assert db_session.query(ApiKey).count() == 0

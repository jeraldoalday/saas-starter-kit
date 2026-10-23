"""Unit-level cover for refresh-session tracking, rotation and revocation.

The endpoint tests (tests/test_sessions.py) exercise the HTTP surface. This
file stays at the service so the security-relevant behaviour — what stops a
token working, and when — is asserted directly rather than inferred from a
status code.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import create_refresh_token, hash_password
from app.models.auth_session import AuthSession
from app.models.user import User
from app.services import session_service
from app.services.session_service import (
    MAX_ACTIVE_SESSIONS_PER_USER,
    SessionNotFoundError,
    authenticate,
    count_active_sessions,
    hash_refresh_token,
    list_sessions,
    purge_expired_sessions,
    revoke_all,
    revoke_session,
    rotate,
    start_session,
)


@pytest.fixture
def user(db_session) -> User:
    account = User(
        email="sessions@example.com",
        hashed_password=hash_password("s3cret-password"),
        full_name="Session Holder",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


@pytest.fixture
def other_user(db_session) -> User:
    account = User(
        email="other-sessions@example.com",
        hashed_password=hash_password("s3cret-password"),
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _token(user: User) -> str:
    return create_refresh_token(str(user.id))


def test_starting_a_session_stores_only_the_digest(db_session, user):
    raw = _token(user)

    session = start_session(db_session, user, raw)

    assert session.token_hash == hash_refresh_token(raw)
    # The raw token must not be recoverable from the row.
    stored = db_session.query(AuthSession).one()
    assert raw not in (stored.token_hash, str(stored.user_agent), str(stored.ip_address))


def test_session_expiry_follows_the_tokens_own_claim(db_session, user):
    """The row and the token must not be able to disagree about expiry."""
    from app.core.config import get_settings

    session = start_session(db_session, user, _token(user))

    expected = datetime.now(UTC) + timedelta(
        days=get_settings().refresh_token_expire_days
    )
    delta = abs((session.expires_at.replace(tzinfo=UTC) - expected).total_seconds())
    assert delta < 60


def test_session_expiry_falls_back_when_exp_claim_is_unreadable(db_session, user):
    """An exp claim datetime.fromtimestamp chokes on must not crash a login.

    Unreachable for tokens this application mints, but a row still has to
    get an expiry from somewhere — the configured lifetime, not a 500.
    """
    from jose import jwt

    from app.core.config import get_settings

    settings = get_settings()
    raw = jwt.encode(
        {"sub": str(user.id), "type": "refresh", "exp": 99999999999999},
        settings.secret_key,
        algorithm=settings.algorithm,
    )

    session = start_session(db_session, user, raw)

    expected = datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
    delta = abs((session.expires_at.replace(tzinfo=UTC) - expected).total_seconds())
    assert delta < 60


def test_context_is_recorded_and_truncated(db_session, user):
    session = start_session(
        db_session,
        user,
        _token(user),
        user_agent="U" * 500,
        ip_address="203.0.113.7",
    )

    # Truncated rather than rejected: a header nobody validates must not be
    # able to fail a login.
    assert len(session.user_agent) == 255
    assert session.ip_address == "203.0.113.7"


def test_blank_context_is_stored_as_null(db_session, user):
    session = start_session(db_session, user, _token(user), user_agent="   ")

    assert session.user_agent is None


def test_authenticate_resolves_a_live_token(db_session, user):
    raw = _token(user)
    started = start_session(db_session, user, raw)

    assert authenticate(db_session, raw).id == started.id


def test_authenticate_rejects_an_unknown_token(db_session, user):
    start_session(db_session, user, _token(user))

    assert authenticate(db_session, "not-a-real-token") is None


def test_authenticate_rejects_a_revoked_session(db_session, user):
    raw = _token(user)
    session = start_session(db_session, user, raw)

    revoke_session(db_session, user, session.id)

    assert authenticate(db_session, raw) is None


def test_authenticate_rejects_an_expired_session(db_session, user):
    raw = _token(user)
    session = start_session(db_session, user, raw)
    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    assert authenticate(db_session, raw) is None


def test_authenticate_rejects_a_deactivated_account(db_session, user):
    raw = _token(user)
    start_session(db_session, user, raw)

    user.is_active = False
    db_session.commit()

    # Otherwise a deactivated account keeps minting access tokens for weeks.
    assert authenticate(db_session, raw) is None


def test_rotation_retires_the_previous_token(db_session, user):
    first = _token(user)
    session = start_session(db_session, user, first)
    second = create_refresh_token(str(user.id)) + "-distinct"

    rotate(db_session, session, second)

    # The old token is retired by the overwrite, not by a separate delete.
    assert authenticate(db_session, first) is None
    assert authenticate(db_session, second) is not None


def test_rotation_reuses_one_row(db_session, user):
    session = start_session(db_session, user, _token(user))

    rotate(db_session, session, "rotated-token-value")

    assert db_session.query(AuthSession).count() == 1
    assert list_sessions(db_session, user)[0].id == session.id


def test_rotation_advances_last_used(db_session, user):
    session = start_session(db_session, user, _token(user))
    before = session.last_used_at

    later = datetime.now(UTC) + timedelta(hours=1)
    rotate(db_session, session, "rotated-token-value", now=later)

    assert session.last_used_at != before


def test_revocation_is_idempotent(db_session, user):
    session = start_session(db_session, user, _token(user))

    first = revoke_session(db_session, user, session.id).revoked_at
    second = revoke_session(db_session, user, session.id).revoked_at

    # The interesting moment is when it stopped working, not when someone
    # last clicked the button.
    assert first == second


def test_cannot_revoke_another_accounts_session(db_session, user, other_user):
    session = start_session(db_session, other_user, _token(other_user))

    with pytest.raises(SessionNotFoundError):
        revoke_session(db_session, user, session.id)

    assert session.revoked_at is None


def test_revoking_an_unknown_id_raises(db_session, user):
    with pytest.raises(SessionNotFoundError):
        revoke_session(db_session, user, uuid.uuid4())


def test_revoke_all_ends_every_live_session(db_session, user):
    for _ in range(3):
        start_session(db_session, user, _token(user) + uuid.uuid4().hex)

    assert revoke_all(db_session, user) == 3
    assert count_active_sessions(db_session, user) == 0


def test_revoke_all_can_spare_the_caller(db_session, user):
    keep = start_session(db_session, user, _token(user) + uuid.uuid4().hex)
    start_session(db_session, user, _token(user) + uuid.uuid4().hex)

    assert revoke_all(db_session, user, except_session_id=keep.id) == 1

    remaining = [s for s in list_sessions(db_session, user) if s.is_active]
    assert [s.id for s in remaining] == [keep.id]


def test_revoke_all_leaves_other_accounts_alone(db_session, user, other_user):
    start_session(db_session, other_user, _token(other_user))
    start_session(db_session, user, _token(user) + uuid.uuid4().hex)

    revoke_all(db_session, user)

    assert count_active_sessions(db_session, other_user) == 1


def test_revoke_all_counts_only_what_it_changed(db_session, user):
    session = start_session(db_session, user, _token(user))
    revoke_session(db_session, user, session.id)

    # Already-revoked sessions are not re-counted.
    assert revoke_all(db_session, user) == 0


def test_the_session_cap_retires_the_oldest(db_session, user):
    started = [
        start_session(db_session, user, _token(user) + uuid.uuid4().hex)
        for _ in range(MAX_ACTIVE_SESSIONS_PER_USER + 2)
    ]

    # A correct password is never refused because of housekeeping; the
    # oldest sessions give way instead.
    assert count_active_sessions(db_session, user) == MAX_ACTIVE_SESSIONS_PER_USER
    assert not started[0].is_active
    assert started[-1].is_active


def test_listing_is_newest_first_and_keeps_revoked_rows(db_session, user):
    old = start_session(db_session, user, _token(user) + uuid.uuid4().hex)
    new = start_session(db_session, user, _token(user) + uuid.uuid4().hex)
    revoke_session(db_session, user, old.id)

    listed = list_sessions(db_session, user)

    assert [s.id for s in listed] == [new.id, old.id]
    assert listed[1].revoked_at is not None


def test_deleting_an_account_takes_its_sessions(db_session, user):
    start_session(db_session, user, _token(user))

    db_session.delete(user)
    db_session.commit()

    # Otherwise the row outlives the account and its token keeps refreshing.
    assert db_session.query(AuthSession).count() == 0


def test_prune_removes_long_revoked_sessions(db_session, user):
    session = start_session(db_session, user, _token(user))
    session.revoked_at = datetime.now(UTC) - timedelta(days=120)
    db_session.commit()

    assert purge_expired_sessions(db_session, older_than_days=90) == 1
    assert db_session.query(AuthSession).count() == 0


def test_prune_removes_long_expired_sessions(db_session, user):
    session = start_session(db_session, user, _token(user))
    session.expires_at = datetime.now(UTC) - timedelta(days=120)
    db_session.commit()

    assert purge_expired_sessions(db_session, older_than_days=90) == 1


def test_prune_keeps_recently_revoked_sessions(db_session, user):
    session = start_session(db_session, user, _token(user))
    revoke_session(db_session, user, session.id)

    # "Signed out on the 3rd" has to stay answerable for a while.
    assert purge_expired_sessions(db_session, older_than_days=90) == 0


def test_prune_keeps_live_sessions(db_session, user):
    start_session(db_session, user, _token(user))

    assert purge_expired_sessions(db_session, older_than_days=1) == 0


class TestDeploymentWideActiveCount:
    """The admin overview's figure, counted in SQL rather than in Python.

    It has to give the same answer as the per-account count, which folds in
    expiry through the model. These pin the two together, because the SQL
    restates liveness and a restatement is exactly the kind of thing that
    drifts.
    """

    def _session(self, db_session, user, *, expires_in_days=14, revoked=False):
        now = datetime.now(UTC)
        row = AuthSession(
            user_id=user.id,
            token_hash=uuid.uuid4().hex,
            created_at=now,
            last_used_at=now,
            expires_at=now + timedelta(days=expires_in_days),
            revoked_at=now if revoked else None,
        )
        db_session.add(row)
        db_session.commit()
        return row

    def test_counts_only_the_live_ones(self, db_session, user):
        self._session(db_session, user)
        self._session(db_session, user)
        self._session(db_session, user, revoked=True)
        self._session(db_session, user, expires_in_days=-1)

        assert session_service.count_all_active_sessions(db_session) == 2

    def test_agrees_with_the_per_account_count(self, db_session, user):
        for _ in range(3):
            self._session(db_session, user)
        self._session(db_session, user, revoked=True)

        # One account here, so the two must land on the same number. They
        # arrive at it by different routes: a WHERE clause and the model's
        # own is_usable.
        assert session_service.count_all_active_sessions(
            db_session
        ) == session_service.count_active_sessions(db_session, user)

    def test_spans_accounts(self, db_session, user):
        other = User(
            email="second-account@example.com",
            hashed_password=hash_password("s3cret-password"),
        )
        db_session.add(other)
        db_session.commit()

        self._session(db_session, user)
        self._session(db_session, other)

        assert session_service.count_all_active_sessions(db_session) == 2

    def test_an_empty_table_counts_zero(self, db_session):
        assert session_service.count_all_active_sessions(db_session) == 0

    def test_a_naive_cutoff_is_read_as_utc(self, db_session, user):
        self._session(db_session, user)

        # SQLite's driver ignores tzinfo, so a cutoff in another zone would
        # otherwise be compared as though its wall-clock reading were UTC.
        naive = datetime.now(UTC).replace(tzinfo=None)
        assert session_service.count_all_active_sessions(db_session, now=naive) == 1

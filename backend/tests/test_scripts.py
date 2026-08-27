"""The operational CLIs: retention prunes for events, keys and sessions.

These run unattended on a schedule, which is exactly why they are worth
testing — nobody reads the output of a cron job that worked, and a prune
that silently deletes the wrong rows looks identical to one that worked.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import hash_password
from app.models.api_key import ApiKey
from app.models.usage_event import UsageEvent
from app.models.user import User
from app.scripts import prune_api_keys, prune_usage_events
from app.services.api_key_service import create_api_key, revoke_api_key


@pytest.fixture
def user(db_session) -> User:
    account = User(
        email="ops@example.com", hashed_password=hash_password("s3cret-password")
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


@pytest.fixture(autouse=True)
def _use_the_test_database(monkeypatch, session_factory):
    for module in (prune_usage_events, prune_api_keys):
        monkeypatch.setattr(module, "SessionLocal", session_factory)


def _seed_events(db_session, *ages_in_days: int) -> None:
    now = datetime.now(UTC)
    for age in ages_in_days:
        db_session.add(
            UsageEvent(
                name="dashboard.viewed",
                source="frontend",
                properties={},
                occurred_at=now - timedelta(days=age),
            )
        )
    db_session.commit()


# --- usage events ---------------------------------------------------------


def test_event_prune_deletes_only_rows_past_the_window(db_session, capsys):
    _seed_events(db_session, 400, 10)

    assert prune_usage_events.main(["--days", "90"]) == 0

    assert "Deleted 1 event" in capsys.readouterr().out
    assert db_session.query(UsageEvent).count() == 1


def test_event_prune_dry_run_deletes_nothing(db_session, capsys):
    _seed_events(db_session, 400, 401)

    assert prune_usage_events.main(["--days", "90", "--dry-run"]) == 0

    assert "2 event(s)" in capsys.readouterr().out
    assert db_session.query(UsageEvent).count() == 2


def test_event_prune_defaults_to_the_configured_retention(db_session, capsys):
    # ANALYTICS_RETENTION_DAYS defaults to 400, so a 300-day-old row stays.
    _seed_events(db_session, 300)

    assert prune_usage_events.main([]) == 0

    assert db_session.query(UsageEvent).count() == 1


def test_event_prune_rejects_a_nonsensical_window(db_session, capsys):
    _seed_events(db_session, 400)

    assert prune_usage_events.main(["--days", "0"]) == 1

    assert "at least 1 day" in capsys.readouterr().err
    assert db_session.query(UsageEvent).count() == 1


# --- revoked API keys -----------------------------------------------------


def test_key_prune_removes_only_old_tombstones(db_session, user, capsys):
    now = datetime.now(UTC)
    live, _ = create_api_key(db_session, user, name="Live")
    old, _ = create_api_key(db_session, user, name="Long revoked")
    revoke_api_key(db_session, user, old.id, now=now - timedelta(days=200))

    assert prune_api_keys.main(["--days", "90"]) == 0

    assert "Deleted 1 revoked key" in capsys.readouterr().out
    assert [key.id for key in db_session.query(ApiKey).all()] == [live.id]


def test_key_prune_leaves_expired_keys_alone(db_session, user):
    """An expired key stops working on its own, and its owner still needs to
    see it listed to understand why their script started failing."""
    now = datetime.now(UTC)
    create_api_key(
        db_session, user, name="Expired", expires_in_days=1, now=now - timedelta(days=300)
    )

    assert prune_api_keys.main(["--days", "90"]) == 0

    assert db_session.query(ApiKey).count() == 1


def test_key_prune_dry_run_deletes_nothing(db_session, user, capsys):
    now = datetime.now(UTC)
    key, _ = create_api_key(db_session, user, name="Long revoked")
    revoke_api_key(db_session, user, key.id, now=now - timedelta(days=200))

    assert prune_api_keys.main(["--days", "90", "--dry-run"]) == 0

    assert "1 revoked key(s)" in capsys.readouterr().out
    assert db_session.query(ApiKey).count() == 1


def test_key_prune_rejects_a_nonsensical_window(db_session, user, capsys):
    assert prune_api_keys.main(["--days", "-5"]) == 1

    assert "at least 1 day" in capsys.readouterr().err


class TestPruneSessions:
    """The retention job for dead sign-in sessions."""

    @staticmethod
    def _user(db_session):
        from app.core.security import hash_password
        from app.models.user import User

        account = User(
            email=f"prune-{uuid.uuid4().hex}@example.com",
            hashed_password=hash_password("s3cret-password"),
        )
        db_session.add(account)
        db_session.commit()
        db_session.refresh(account)
        return account

    @staticmethod
    def _session_row(db_session, user, *, revoked_days_ago=None, expired_days_ago=None):
        from app.models.auth_session import AuthSession

        now = datetime.now(UTC)
        row = AuthSession(
            user_id=user.id,
            token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            created_at=now - timedelta(days=200),
            last_used_at=now - timedelta(days=200),
            expires_at=(
                now - timedelta(days=expired_days_ago)
                if expired_days_ago is not None
                else now + timedelta(days=14)
            ),
            revoked_at=(
                now - timedelta(days=revoked_days_ago)
                if revoked_days_ago is not None
                else None
            ),
        )
        db_session.add(row)
        db_session.commit()
        return row

    def test_dry_run_reports_without_deleting(
        self, db_session, session_factory, monkeypatch, capsys
    ):
        from app.models.auth_session import AuthSession
        from app.scripts import prune_sessions

        monkeypatch.setattr(prune_sessions, "SessionLocal", session_factory)
        user = self._user(db_session)
        self._session_row(db_session, user, revoked_days_ago=90)

        assert prune_sessions.main(["--dry-run", "--days", "30"]) == 0

        assert "1 dead session(s)" in capsys.readouterr().out
        assert db_session.query(AuthSession).count() == 1

    def test_deletes_long_dead_sessions(
        self, db_session, session_factory, monkeypatch, capsys
    ):
        from app.models.auth_session import AuthSession
        from app.scripts import prune_sessions

        monkeypatch.setattr(prune_sessions, "SessionLocal", session_factory)
        user = self._user(db_session)
        self._session_row(db_session, user, revoked_days_ago=90)
        self._session_row(db_session, user, expired_days_ago=90)
        live = self._session_row(db_session, user)

        assert prune_sessions.main(["--days", "30"]) == 0

        assert "Deleted 2 dead session(s)" in capsys.readouterr().out
        remaining = db_session.query(AuthSession).all()
        assert [row.id for row in remaining] == [live.id]

    def test_also_clears_spent_one_time_tokens(
        self, db_session, session_factory, monkeypatch, capsys
    ):
        from app.models.token import OneTimeToken, TokenPurpose
        from app.scripts import prune_sessions
        from app.services.token_service import consume_token, issue_token

        monkeypatch.setattr(prune_sessions, "SessionLocal", session_factory)
        user = self._user(db_session)
        raw = issue_token(db_session, user, TokenPurpose.EMAIL_VERIFICATION)
        consume_token(db_session, raw, TokenPurpose.EMAIL_VERIFICATION)

        assert prune_sessions.main(["--days", "30"]) == 0

        assert "1 spent or expired one-time token(s)" in capsys.readouterr().out
        assert db_session.query(OneTimeToken).count() == 0

    def test_rejects_a_nonsensical_window(self, session_factory, monkeypatch):
        from app.scripts import prune_sessions

        monkeypatch.setattr(prune_sessions, "SessionLocal", session_factory)

        assert prune_sessions.main(["--days", "0"]) == 1


class TestAuditLogPrune:
    """The trail is append-only, so somebody has to take the other side.

    Its window is a separate policy decision from the session one, which is
    why it gets its own entry point rather than riding along with another
    prune, and why these tests check the default is read from its own
    setting.
    """

    @pytest.fixture(autouse=True)
    def _use_the_test_database(self, monkeypatch, session_factory):
        from app.scripts import prune_audit_logs

        monkeypatch.setattr(prune_audit_logs, "SessionLocal", session_factory)

    def _seed(self, db_session, user, *ages_in_days: int) -> None:
        from app.models.audit_log import AuditAction, AuditLog

        now = datetime.now(UTC)
        for age in ages_in_days:
            db_session.add(
                AuditLog(
                    user_id=user.id,
                    action=AuditAction.LOGIN.value,
                    outcome="success",
                    context={},
                    created_at=now - timedelta(days=age),
                )
            )
        db_session.commit()

    def test_deletes_only_entries_past_the_window(self, db_session, user, capsys):
        from app.models.audit_log import AuditLog
        from app.scripts import prune_audit_logs

        self._seed(db_session, user, 400, 200, 10)

        assert prune_audit_logs.main(["--days", "180"]) == 0

        assert "Deleted 2" in capsys.readouterr().out
        assert db_session.query(AuditLog).count() == 1

    def test_dry_run_counts_without_deleting(self, db_session, user, capsys):
        from app.models.audit_log import AuditLog
        from app.scripts import prune_audit_logs

        self._seed(db_session, user, 400, 200, 10)

        assert prune_audit_logs.main(["--dry-run", "--days", "180"]) == 0

        assert "2 audit entr(ies)" in capsys.readouterr().out
        # The point of a dry run is that it is safe to run at any time.
        assert db_session.query(AuditLog).count() == 3

    def test_defaults_to_the_configured_retention(self, db_session, user, capsys):
        from app.core.config import get_settings
        from app.scripts import prune_audit_logs

        self._seed(db_session, user, get_settings().audit_retention_days + 5, 1)

        assert prune_audit_logs.main([]) == 0

        assert "Deleted 1" in capsys.readouterr().out

    def test_rejects_a_nonsensical_window(self, capsys):
        from app.scripts import prune_audit_logs

        # Refused rather than clamped: "--days 0" is somebody expecting the
        # table emptied, and quietly keeping a day would look like a no-op.
        assert prune_audit_logs.main(["--days", "0"]) == 1
        assert "at least 1 day" in capsys.readouterr().err

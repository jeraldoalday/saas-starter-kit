"""The operational CLIs: retention prunes for events and revoked keys.

These run unattended on a schedule, which is exactly why they are worth
testing — nobody reads the output of a cron job that worked, and a prune
that silently deletes the wrong rows looks identical to one that worked.
"""

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

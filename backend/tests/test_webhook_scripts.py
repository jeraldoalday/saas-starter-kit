"""The two webhook CLIs: the delivery worker and the retention prune.

These run unattended, which is the reason they are worth testing — nobody
reads the output of a cron job that worked, so a worker that quietly stopped
delivering and a worker that had nothing to deliver look identical from
outside.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import hash_password
from app.models.user import User
from app.models.webhook_delivery import DeliveryStatus, WebhookDelivery
from app.models.webhook_endpoint import WebhookEndpoint, WebhookEventType
from app.scripts import deliver_webhooks, prune_webhook_deliveries
from app.services.webhooks.dispatch import DeliveryResult, queue_delivery

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _use_the_test_database(monkeypatch, session_factory):
    for module in (deliver_webhooks, prune_webhook_deliveries):
        monkeypatch.setattr(module, "SessionLocal", session_factory)


@pytest.fixture
def endpoint(db_session) -> WebhookEndpoint:
    user = User(
        email="ops-hooks@example.com", hashed_password=hash_password("s3cret-password")
    )
    db_session.add(user)
    db_session.commit()

    row = WebhookEndpoint(
        user_id=user.id,
        url="https://receiver.example.com/hooks",
        secret="whsec_test-secret",
        event_types=[],
        created_at=NOW,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _queue(db_session, endpoint, *, count=1, age_days=0):
    created = datetime.now(UTC) - timedelta(days=age_days)
    return [
        queue_delivery(db_session, endpoint, WebhookEventType.PING, {}, now=created)
        for _ in range(count)
    ]


def _always(result: DeliveryResult):
    return lambda url, body, headers, timeout: result


# --- the delivery worker --------------------------------------------------


def test_a_pass_delivers_what_is_due(db_session, endpoint, capsys, monkeypatch):
    monkeypatch.setattr(
        deliver_webhooks, "deliver_due", _pass_through(DeliveryResult(True, 200, None))
    )
    _queue(db_session, endpoint, count=2)

    assert deliver_webhooks.main([]) == 0

    out = capsys.readouterr().out
    assert "attempted=2" in out
    assert "delivered=2" in out


def _pass_through(result: DeliveryResult):
    """Wrap deliver_due so the CLI's own session gets a stub transport.

    The worker builds its session from SessionLocal and calls deliver_due
    with the default transport; there is no flag for injecting one, because
    production has no reason to. So the seam for the test is the function,
    with the real one called underneath.
    """
    from app.services.webhooks.dispatch import deliver_due as real_deliver_due

    def _wrapped(session, *, limit=None, transport=None, now=None):
        return real_deliver_due(
            session, limit=limit, transport=_always(result), now=now
        )

    return _wrapped


def test_a_failing_receiver_leaves_the_delivery_queued(
    db_session, endpoint, capsys, monkeypatch
):
    monkeypatch.setattr(
        deliver_webhooks, "deliver_due", _pass_through(DeliveryResult(False, 503, "HTTP 503"))
    )
    _queue(db_session, endpoint)

    assert deliver_webhooks.main([]) == 0

    assert "retrying=1" in capsys.readouterr().out
    delivery = db_session.query(WebhookDelivery).one()
    assert delivery.status == DeliveryStatus.PENDING.value


def test_a_pass_is_bounded_by_the_limit_flag(db_session, endpoint, capsys, monkeypatch):
    monkeypatch.setattr(
        deliver_webhooks, "deliver_due", _pass_through(DeliveryResult(True, 200, None))
    )
    _queue(db_session, endpoint, count=5)

    deliver_webhooks.main(["--limit", "2"])

    assert "attempted=2" in capsys.readouterr().out


def test_a_dry_run_counts_without_sending(db_session, endpoint, capsys):
    _queue(db_session, endpoint, count=3)

    assert deliver_webhooks.main(["--dry-run"]) == 0

    assert "3 delivery(s) due now" in capsys.readouterr().out
    # Nothing was attempted, which is the half of "dry run" that matters.
    assert all(row.attempts == 0 for row in db_session.query(WebhookDelivery).all())


def test_a_dry_run_says_when_there_is_more_than_a_batch(db_session, endpoint, capsys):
    """"50" and "at least 50" are different operational situations."""
    _queue(db_session, endpoint, count=4)

    deliver_webhooks.main(["--dry-run", "--limit", "3"])

    assert "3 or more" in capsys.readouterr().out


def test_an_empty_queue_reports_a_quiet_pass(db_session, endpoint, capsys):
    assert deliver_webhooks.main([]) == 0

    assert "attempted=0" in capsys.readouterr().out


def test_loop_runs_a_pass_and_stops_cleanly_on_interrupt(
    db_session, endpoint, capsys, monkeypatch
):
    """--loop is how this runs as a long-lived worker instead of a cron job.

    Ctrl-C is the ordinary way someone stops a worker started by hand, so it
    must exit 0 with a short message - not a stack trace - and it must have
    actually attempted the pass already queued before sleeping.
    """
    monkeypatch.setattr(
        deliver_webhooks, "deliver_due", _pass_through(DeliveryResult(True, 200, None))
    )
    _queue(db_session, endpoint)

    def _raise(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(deliver_webhooks.time, "sleep", _raise)

    assert deliver_webhooks.main(["--loop"]) == 0

    captured = capsys.readouterr()
    assert "attempted=1" in captured.out
    assert "Stopped." in captured.err


def test_loop_floors_a_too_small_interval(db_session, endpoint, capsys, monkeypatch):
    """A worker polling faster than the floor would spend more time asking
    the database whether anything is due than any webhook's latency budget
    could justify."""
    monkeypatch.setattr(
        deliver_webhooks, "deliver_due", _pass_through(DeliveryResult(True, 200, None))
    )

    def _raise(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(deliver_webhooks.time, "sleep", _raise)

    deliver_webhooks.main(["--loop", "--interval", "0"])

    assert f"every {deliver_webhooks.MIN_INTERVAL_SECONDS}s" in capsys.readouterr().err


def test_the_worker_warns_when_queueing_is_switched_off(
    db_session, endpoint, capsys, monkeypatch
):
    """It still drains: turning the flag off stops new promises being made,
    not the ones already made."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "webhooks_enabled", False)

    assert deliver_webhooks.main([]) == 0

    assert "WEBHOOKS_ENABLED is false" in capsys.readouterr().err


# --- the retention prune --------------------------------------------------


def test_the_prune_deletes_finished_rows_past_the_window(db_session, endpoint, capsys):
    for delivery in _queue(db_session, endpoint, count=2, age_days=60):
        delivery.status = DeliveryStatus.DELIVERED.value
    db_session.commit()

    assert prune_webhook_deliveries.main(["--days", "30"]) == 0

    assert "Deleted 2 finished delivery(s)" in capsys.readouterr().out


def test_the_prune_leaves_pending_rows_alone(db_session, endpoint, capsys):
    _queue(db_session, endpoint, age_days=400)

    assert prune_webhook_deliveries.main(["--days", "30"]) == 0

    assert "Deleted 0 finished delivery(s)" in capsys.readouterr().out
    assert db_session.query(WebhookDelivery).count() == 1


def test_a_prune_dry_run_reports_without_deleting(db_session, endpoint, capsys):
    delivery = _queue(db_session, endpoint, age_days=60)[0]
    delivery.status = DeliveryStatus.FAILED.value
    db_session.commit()

    assert prune_webhook_deliveries.main(["--days", "30", "--dry-run"]) == 0

    assert "1 finished delivery(s) older than 30 day(s) would be deleted" in (
        capsys.readouterr().out
    )
    assert db_session.query(WebhookDelivery).count() == 1


def test_a_nonsense_window_is_refused(capsys):
    """A zero-day window would delete everything finished, which is not what
    anyone who typed it meant."""
    assert prune_webhook_deliveries.main(["--days", "0"]) == 1

    assert "at least 1 day" in capsys.readouterr().err

"""The out-of-process readiness watcher.

Worth testing for the same reason as the prune CLIs: it runs unattended, so
the difference between "checked and everything was fine" and "never
actually checked" is invisible in the output of a job that exited 0.
"""

import pytest
from sqlalchemy.exc import OperationalError

from app.scripts import check_health_and_alert
from app.services.alerts import (
    AlertRouter,
    InMemoryAlertSender,
    Severity,
    set_alert_router,
)


@pytest.fixture
def sender() -> InMemoryAlertSender:
    collected = InMemoryAlertSender()
    set_alert_router(AlertRouter(collected, min_severity=Severity.INFO))
    yield collected
    set_alert_router(None)


@pytest.fixture(autouse=True)
def _use_the_test_database(monkeypatch, session_factory):
    monkeypatch.setattr(check_health_and_alert, "SessionLocal", session_factory)


def _break_the_database(monkeypatch):
    class _BrokenSession:
        def execute(self, *_args, **_kwargs):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        def close(self):
            return None

    monkeypatch.setattr(check_health_and_alert, "SessionLocal", lambda: _BrokenSession())


def test_a_healthy_database_alerts_nobody(sender, capsys):
    assert check_health_and_alert.main([]) == 0

    assert "ok: database reachable" in capsys.readouterr().out
    assert sender.sent == []


def test_an_unreachable_database_pages_and_fails(sender, monkeypatch, capsys):
    _break_the_database(monkeypatch)

    exit_code = check_health_and_alert.main([])

    # Non-zero so the scheduler's own failure reporting agrees with the
    # alert instead of recording a clean run.
    assert exit_code == 1
    assert "database unreachable" in capsys.readouterr().err
    assert sender.last().severity is Severity.CRITICAL
    assert "database unreachable" in sender.last().title.lower()


def test_the_smoke_test_alerts_on_a_healthy_database(sender, capsys):
    exit_code = check_health_and_alert.main(["--always-alert"])

    assert exit_code == 0
    assert "delivered=True" in capsys.readouterr().out
    assert len(sender.sent) == 1


def test_the_smoke_test_delivers_every_time(sender):
    """Suppression must not make the second run of a routing test silent —
    that is exactly when someone is checking whether they fixed the URL."""
    check_health_and_alert.main(["--always-alert"])
    check_health_and_alert.main(["--always-alert"])

    assert len(sender.sent) == 2


def test_a_failed_delivery_does_not_crash_the_watcher(monkeypatch, capsys):
    class _BrokenSender:
        def send(self, alert):
            raise RuntimeError("pager is on fire")

    set_alert_router(AlertRouter(_BrokenSender(), min_severity=Severity.INFO))
    try:
        _break_the_database(monkeypatch)

        assert check_health_and_alert.main([]) == 1
        assert "delivered=False" in capsys.readouterr().err
    finally:
        set_alert_router(None)

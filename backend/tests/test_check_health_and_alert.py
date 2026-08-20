from sqlalchemy.exc import OperationalError

from app.scripts import check_health_and_alert
from app.services.alerting import InMemoryAlertSender


def _patch_sender(monkeypatch) -> InMemoryAlertSender:
    sender = InMemoryAlertSender()
    monkeypatch.setattr(check_health_and_alert, "get_alert_sender", lambda: sender)
    return sender


def test_main_returns_zero_and_sends_no_alert_when_healthy(monkeypatch, capsys):
    monkeypatch.setattr(check_health_and_alert, "check_database", lambda: None)
    sender = _patch_sender(monkeypatch)

    exit_code = check_health_and_alert.main([])

    assert exit_code == 0
    assert sender.sent == []
    assert "ok" in capsys.readouterr().out


def test_main_alerts_and_returns_nonzero_when_unhealthy(monkeypatch, capsys):
    monkeypatch.setattr(
        check_health_and_alert, "check_database", lambda: "connection refused"
    )
    sender = _patch_sender(monkeypatch)

    exit_code = check_health_and_alert.main([])

    assert exit_code == 1
    assert len(sender.sent) == 1
    assert sender.sent[0].severity == "critical"
    assert "connection refused" in sender.sent[0].body
    assert "alert" in capsys.readouterr().err


def test_main_always_alert_sends_test_alert_when_healthy(monkeypatch, capsys):
    monkeypatch.setattr(check_health_and_alert, "check_database", lambda: None)
    sender = _patch_sender(monkeypatch)

    exit_code = check_health_and_alert.main(["--always-alert"])

    assert exit_code == 0
    assert len(sender.sent) == 1
    assert sender.sent[0].severity == "info"


def test_check_database_returns_none_when_reachable(monkeypatch):
    class _WorkingSession:
        def execute(self, *args, **kwargs):
            return None

        def close(self):
            pass

    monkeypatch.setattr(check_health_and_alert, "SessionLocal", lambda: _WorkingSession())

    assert check_health_and_alert.check_database() is None


def test_check_database_returns_reason_on_failure(monkeypatch):
    class _BrokenSession:
        def execute(self, *args, **kwargs):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        def close(self):
            pass

    monkeypatch.setattr(check_health_and_alert, "SessionLocal", lambda: _BrokenSession())

    reason = check_health_and_alert.check_database()

    assert reason is not None
    assert "connection refused" in reason

import urllib.error

import pytest

from app.services.alerting import InMemoryAlertSender
from app.services.alerting.base import AlertMessage
from app.services.alerting.console import ConsoleAlertSender
from app.services.alerting.webhook import WebhookAlertSender


def _message(subject: str = "Subject") -> AlertMessage:
    return AlertMessage(subject=subject, body="Body", severity="critical")


def test_console_sender_reports_success():
    assert ConsoleAlertSender().send(_message()) is True


def test_memory_sender_collects_messages():
    sender = InMemoryAlertSender()

    sender.send(_message("one"))
    sender.send(_message("two"))

    assert len(sender.sent) == 2
    assert sender.sent[0].subject == "one"


def test_memory_sender_clear_empties_sent():
    sender = InMemoryAlertSender()
    sender.send(_message())

    sender.clear()

    assert sender.sent == []


def test_webhook_sender_posts_json(monkeypatch):
    captured = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def _urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = request.data
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("app.services.alerting.webhook.urllib.request.urlopen", _urlopen)

    sender = WebhookAlertSender(url="https://hooks.example.com/alert", timeout=3)
    result = sender.send(_message("db down"))

    assert result is True
    assert captured["url"] == "https://hooks.example.com/alert"
    assert captured["timeout"] == 3
    assert captured["headers"]["Content-type"] == "application/json"
    assert b"db down" in captured["body"]


@pytest.mark.parametrize(
    "exc",
    [
        urllib.error.URLError("connection refused"),
        OSError("network unreachable"),
    ],
)
def test_webhook_sender_returns_false_instead_of_raising(monkeypatch, exc):
    """A down paging provider must not crash whatever raised the alert."""

    def _explode(request, timeout):
        raise exc

    monkeypatch.setattr("app.services.alerting.webhook.urllib.request.urlopen", _explode)

    sender = WebhookAlertSender(url="https://hooks.example.com/alert")

    assert sender.send(_message()) is False


def test_factory_defaults_to_console(monkeypatch):
    from app.services.alerting import factory

    factory.get_alert_sender.cache_clear()
    monkeypatch.setattr(factory.get_settings(), "alert_backend", "console")

    assert isinstance(factory.get_alert_sender(), ConsoleAlertSender)
    factory.get_alert_sender.cache_clear()


def test_factory_returns_webhook_sender_when_configured(monkeypatch):
    from app.services.alerting import factory

    factory.get_alert_sender.cache_clear()
    settings = factory.get_settings()
    monkeypatch.setattr(settings, "alert_backend", "webhook")
    monkeypatch.setattr(settings, "alert_webhook_url", "https://hooks.example.com/alert")

    sender = factory.get_alert_sender()

    assert isinstance(sender, WebhookAlertSender)
    assert sender.url == "https://hooks.example.com/alert"
    factory.get_alert_sender.cache_clear()

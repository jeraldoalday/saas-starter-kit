import smtplib

import pytest

from app.services.email import InMemoryEmailSender
from app.services.email.base import EmailMessage
from app.services.email.console import ConsoleEmailSender
from app.services.email.smtp import SmtpEmailSender
from app.services.email.templates import password_reset_email, verification_email


def _message(to: str = "a@example.com") -> EmailMessage:
    return EmailMessage(to=to, subject="Subject", text_body="Body")


def test_console_sender_reports_success():
    assert ConsoleEmailSender().send(_message()) is True


def test_memory_sender_collects_messages():
    sender = InMemoryEmailSender()

    sender.send(_message("one@example.com"))
    sender.send(_message("two@example.com"))

    assert len(sender.outbox) == 2
    assert sender.last_to("one@example.com").to == "one@example.com"
    assert sender.last_to("missing@example.com") is None


def test_memory_sender_clear_empties_outbox():
    sender = InMemoryEmailSender()
    sender.send(_message())

    sender.clear()

    assert sender.outbox == []


def test_smtp_sender_returns_false_instead_of_raising(monkeypatch):
    """A failed send must not fail the request that triggered it."""

    def _explode(*args, **kwargs):
        raise smtplib.SMTPException("connection refused")

    monkeypatch.setattr(smtplib, "SMTP", _explode)
    sender = SmtpEmailSender(host="localhost", port=587)

    assert sender.send(_message()) is False


def test_smtp_sender_survives_os_errors(monkeypatch):
    def _explode(*args, **kwargs):
        raise OSError("network unreachable")

    monkeypatch.setattr(smtplib, "SMTP", _explode)

    assert SmtpEmailSender(host="localhost", port=587).send(_message()) is False


class _FakeSmtp:
    """Stands in for smtplib.SMTP's context-manager protocol."""

    def __init__(self):
        self.started_tls = False
        self.login_args: tuple[str, str] | None = None
        self.sent_message = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, mime):
        self.sent_message = mime


def test_smtp_sender_sends_over_tls_with_login_and_html_alternative(monkeypatch):
    fake = _FakeSmtp()
    monkeypatch.setattr(smtplib, "SMTP", lambda *args, **kwargs: fake)
    sender = SmtpEmailSender(
        host="localhost",
        port=587,
        username="user",
        password="pw",
        from_address="noreply@example.com",
    )
    message = EmailMessage(
        to="a@example.com", subject="Subject", text_body="Body", html_body="<p>Body</p>"
    )

    assert sender.send(message) is True
    assert fake.started_tls is True
    assert fake.login_args == ("user", "pw")
    assert fake.sent_message["To"] == "a@example.com"
    assert fake.sent_message.get_body(preferencelist=("html",)) is not None


def test_smtp_sender_skips_tls_and_login_when_not_configured(monkeypatch):
    fake = _FakeSmtp()
    monkeypatch.setattr(smtplib, "SMTP", lambda *args, **kwargs: fake)
    sender = SmtpEmailSender(host="localhost", port=25, use_tls=False)

    assert sender.send(_message()) is True
    assert fake.started_tls is False
    assert fake.login_args is None


@pytest.mark.parametrize(
    ("builder", "url"),
    [
        (verification_email, "https://app.example.com/verify?token=abc"),
        (password_reset_email, "https://app.example.com/reset?token=abc"),
    ],
)
def test_templates_embed_the_url_in_both_bodies(builder, url):
    message = builder("user@example.com", url)

    assert url in message.text_body
    assert message.html_body is not None
    assert url in message.html_body
    assert message.to == "user@example.com"
    assert message.subject


def test_factory_defaults_to_console(monkeypatch):
    from app.services.email import factory

    factory.get_email_sender.cache_clear()
    monkeypatch.setattr(factory.get_settings(), "email_backend", "console")

    assert isinstance(factory.get_email_sender(), ConsoleEmailSender)
    factory.get_email_sender.cache_clear()

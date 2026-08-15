from functools import lru_cache

from app.core.config import get_settings
from app.services.email.base import EmailSender
from app.services.email.console import ConsoleEmailSender
from app.services.email.smtp import SmtpEmailSender


@lru_cache
def get_email_sender() -> EmailSender:
    """Build the configured transport.

    Defaults to the console transport so a fresh checkout can exercise the
    verification and reset flows without any provider credentials.
    """
    settings = get_settings()

    if settings.email_backend == "smtp":
        return SmtpEmailSender(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            from_address=settings.email_from,
            use_tls=settings.smtp_use_tls,
        )

    return ConsoleEmailSender()

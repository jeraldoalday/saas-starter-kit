from app.services.email.base import EmailMessage, EmailSender
from app.services.email.console import ConsoleEmailSender
from app.services.email.factory import get_email_sender
from app.services.email.memory import InMemoryEmailSender
from app.services.email.smtp import SmtpEmailSender

__all__ = [
    "EmailMessage",
    "EmailSender",
    "ConsoleEmailSender",
    "InMemoryEmailSender",
    "SmtpEmailSender",
    "get_email_sender",
]

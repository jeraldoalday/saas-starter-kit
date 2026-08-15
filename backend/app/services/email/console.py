import logging

from app.services.email.base import EmailMessage

logger = logging.getLogger("app.email")


class ConsoleEmailSender:
    """Logs the email instead of sending it.

    The development default: the verification/reset link lands in the app
    logs, so the flow is exercisable without configuring a provider.
    """

    def send(self, message: EmailMessage) -> bool:
        logger.info(
            "email (console transport)",
            extra={
                "email_to": message.to,
                "email_subject": message.subject,
                "email_body": message.text_body,
            },
        )
        return True

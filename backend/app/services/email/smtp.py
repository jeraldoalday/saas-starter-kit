import logging
import smtplib
from email.message import EmailMessage as MimeMessage

from app.services.email.base import EmailMessage

logger = logging.getLogger("app.email")


class SmtpEmailSender:
    """SMTP transport.

    Works with any SMTP provider (Postmark, SES, Mailgun, Resend all expose
    one), which keeps the scaffold free of a vendor SDK. Swap in a provider's
    HTTP API by writing another EmailSender if you want their analytics.
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str = "",
        password: str = "",
        from_address: str = "",
        use_tls: bool = True,
        timeout: int = 10,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_address = from_address
        self.use_tls = use_tls
        self.timeout = timeout

    def send(self, message: EmailMessage) -> bool:
        mime = MimeMessage()
        mime["From"] = self.from_address
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.text_body)
        if message.html_body:
            mime.add_alternative(message.html_body, subtype="html")

        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
                if self.use_tls:
                    smtp.starttls()
                if self.username:
                    smtp.login(self.username, self.password)
                smtp.send_message(mime)
        except (smtplib.SMTPException, OSError):
            # Never propagate: a failed send must not fail the request that
            # triggered it. The user can request another email.
            logger.exception(
                "email delivery failed", extra={"email_to": message.to}
            )
            return False

        return True

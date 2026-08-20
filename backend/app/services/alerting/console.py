import logging

from app.services.alerting.base import AlertMessage

logger = logging.getLogger("app.alerting")


class ConsoleAlertSender:
    """Logs the alert instead of sending it.

    The development default: nothing pages anyone until alert_webhook_url is
    configured, so a fresh checkout never fires real notifications.
    """

    def send(self, message: AlertMessage) -> bool:
        logger.warning(
            "alert (console transport)",
            extra={
                "alert_subject": message.subject,
                "alert_body": message.body,
                "alert_severity": message.severity,
            },
        )
        return True

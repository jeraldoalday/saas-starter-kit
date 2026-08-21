import logging

from app.services.alerts.base import Alert, AlertSender, Severity

logger = logging.getLogger("app.alerts")

_LOG_LEVELS = {
    Severity.INFO: logging.INFO,
    Severity.WARNING: logging.WARNING,
    Severity.CRITICAL: logging.CRITICAL,
}


class LoggingAlertSender(AlertSender):
    """Write the alert to the structured log and call it delivered.

    The default transport, because it is the one that works with no
    configuration: logs already go somewhere in production, and a deployment
    that has not wired up a webhook yet still leaves a record at the right
    level for a log-based monitor to alert on.
    """

    def send(self, alert: Alert) -> bool:
        logger.log(
            _LOG_LEVELS.get(alert.severity, logging.WARNING),
            alert.title,
            extra={
                "alert_message": alert.message,
                "alert_severity": alert.severity.value,
                "alert_key": alert.dedupe_key,
                **{f"alert_{key}": value for key, value in alert.context.items()},
            },
        )
        return True

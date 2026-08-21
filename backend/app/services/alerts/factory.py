from functools import lru_cache

from app.core.config import get_settings
from app.services.alerts.base import AlertSender
from app.services.alerts.logging import LoggingAlertSender
from app.services.alerts.webhook import WebhookAlertSender


@lru_cache
def get_alert_sender() -> AlertSender:
    """Build the configured transport.

    Defaults to the logging transport, so a deployment that has not chosen
    a paging destination still records every alert at a level a log-based
    monitor can watch — rather than dropping them because nothing was set up.
    """
    settings = get_settings()

    if settings.alert_backend == "webhook" and settings.alert_webhook_url:
        return WebhookAlertSender(
            settings.alert_webhook_url,
            timeout_seconds=settings.alert_webhook_timeout_seconds,
            environment=settings.environment,
        )

    return LoggingAlertSender()

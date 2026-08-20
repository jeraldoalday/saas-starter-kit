from functools import lru_cache

from app.core.config import get_settings
from app.services.alerting.base import AlertSender
from app.services.alerting.console import ConsoleAlertSender
from app.services.alerting.webhook import WebhookAlertSender


@lru_cache
def get_alert_sender() -> AlertSender:
    """Build the configured transport.

    Defaults to the console transport so a fresh checkout never pages
    anyone. Set ALERT_BACKEND=webhook and ALERT_WEBHOOK_URL to route alerts
    to Slack, PagerDuty, Opsgenie or similar.
    """
    settings = get_settings()

    if settings.alert_backend == "webhook":
        return WebhookAlertSender(
            url=settings.alert_webhook_url,
            timeout=settings.alert_webhook_timeout_seconds,
        )

    return ConsoleAlertSender()

"""Deliver alerts to an incoming webhook (Slack, Discord, PagerDuty relay).

urllib rather than a client library: this ships one POST of one JSON object
per incident, and a dependency whose own failures would need alerting about
is a poor foundation for the thing that reports failures.
"""

import json
import logging
import urllib.error
import urllib.request

from app.services.alerts.base import Alert, AlertSender, Severity

logger = logging.getLogger("app.alerts")

# Rendered into the message so an operator can triage from the notification
# without opening a dashboard.
_SEVERITY_MARKERS = {
    Severity.INFO: "INFO",
    Severity.WARNING: "WARN",
    Severity.CRITICAL: "CRITICAL",
}


class WebhookAlertSender(AlertSender):
    """POST one JSON object per alert to a configured URL.

    The payload leads with `text`, which is what Slack-compatible incoming
    webhooks render, and carries the structured fields alongside it for
    anything that parses rather than displays.
    """

    def __init__(self, url: str, *, timeout_seconds: float = 5.0, environment: str = "") -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.environment = environment

    def _text(self, alert: Alert) -> str:
        marker = _SEVERITY_MARKERS.get(alert.severity, alert.severity.value.upper())
        where = f"[{self.environment}] " if self.environment else ""
        return f"{where}[{marker}] {alert.title} — {alert.message}"

    def send(self, alert: Alert) -> bool:
        if not self.url:
            return False

        payload = {"text": self._text(alert), **alert.as_dict()}
        if self.environment:
            payload["environment"] = self.environment

        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            # A timeout is not optional here: without one a wedged webhook
            # endpoint would hold the request thread that raised the alert
            # for as long as the socket stayed open.
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, OSError, ValueError):
            # Swallowed deliberately. Something was already wrong enough to
            # alert about; failing to tell anyone must not also fail the
            # request that noticed.
            logger.exception(
                "failed to deliver alert", extra={"alert_key": alert.dedupe_key}
            )
            return False

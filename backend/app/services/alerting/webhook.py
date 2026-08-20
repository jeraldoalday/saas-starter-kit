import json
import logging
import urllib.error
import urllib.request

from app.services.alerting.base import AlertMessage

logger = logging.getLogger("app.alerting")


class WebhookAlertSender:
    """Posts a JSON payload to a generic incoming webhook.

    Deliberately vendor-agnostic (stdlib only, no paging-provider SDK): a
    Slack incoming webhook, PagerDuty/Opsgenie generic webhook integration,
    or an Alertmanager-style relay all accept a plain JSON POST, so pointing
    alert_webhook_url at one of those is the whole integration.

    The payload carries both a `text` field (what Slack-compatible
    receivers render directly) and the structured subject/body/severity, so
    a receiver that wants to route on severity can do so without parsing
    `text`.
    """

    def __init__(self, url: str, timeout: int = 5) -> None:
        self.url = url
        self.timeout = timeout

    def send(self, message: AlertMessage) -> bool:
        payload = json.dumps(
            {
                "text": f"[{message.severity}] {message.subject}\n{message.body}",
                "subject": message.subject,
                "body": message.body,
                "severity": message.severity,
            }
        ).encode("utf-8")

        request = urllib.request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            # urlopen raises HTTPError (a URLError subclass) on a 4xx/5xx
            # response, so a non-raising return here means the receiver
            # accepted it.
            urllib.request.urlopen(request, timeout=self.timeout)
        except (urllib.error.URLError, OSError, ValueError):
            # Never propagate: a down paging provider must not take out the
            # health check or script that is trying to raise the alarm.
            logger.exception("alert delivery failed", extra={"alert_subject": message.subject})
            return False

        return True

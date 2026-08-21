"""Where an alert is decided on: severity floor, then deduplication.

Both gates exist because the failure mode of alerting is not "no alerts",
it is "so many alerts that the real one scrolls past". A database outage
fires the readiness probe every few seconds; an expired Stripe webhook
secret fires on every delivery attempt. Without suppression the on-call
channel becomes a firehose within a minute of the incident starting, which
is exactly when it needs to be readable.
"""

import logging
import threading
import time

from app.core.config import get_settings
from app.services.alerts.base import Alert, AlertSender, Severity, severity_rank
from app.services.alerts.factory import get_alert_sender

logger = logging.getLogger("app.alerts")


class AlertRouter:
    """Applies the severity floor and per-key suppression, then delivers."""

    def __init__(
        self,
        sender: AlertSender,
        *,
        min_severity: Severity | str = Severity.WARNING,
        suppression_window_seconds: int = 300,
    ) -> None:
        self.sender = sender
        self.min_severity = str(min_severity)
        self.suppression_window_seconds = suppression_window_seconds
        self._last_sent: dict[str, float] = {}
        self._lock = threading.Lock()

    def _should_send(self, alert: Alert, now: float) -> bool:
        if severity_rank(alert.severity) < severity_rank(self.min_severity):
            return False

        if self.suppression_window_seconds <= 0:
            return True

        key = alert.dedupe_key
        with self._lock:
            last = self._last_sent.get(key)
            if last is not None and now - last < self.suppression_window_seconds:
                return False
            self._last_sent[key] = now
            return True

    def dispatch(self, alert: Alert, *, now: float | None = None) -> bool:
        """Deliver the alert, or report why it wasn't.

        Returns False for "filtered out", "suppressed as a repeat" and
        "transport refused it" alike — the caller is raising an alert, not
        managing a delivery pipeline, and none of those are distinctions it
        can act on.
        """
        now = now if now is not None else time.monotonic()

        if not self._should_send(alert, now):
            return False

        try:
            return self.sender.send(alert)
        except Exception:  # noqa: BLE001 - the transport contract is "never raise"
            # A transport that breaks its contract still must not take the
            # caller down with it.
            logger.exception(
                "alert transport raised", extra={"alert_key": alert.dedupe_key}
            )
            return False

    def reset(self) -> None:
        """Forget suppression state. Intended for tests."""
        with self._lock:
            self._last_sent.clear()


_router: AlertRouter | None = None
_router_lock = threading.Lock()


def get_alert_router() -> AlertRouter:
    """The process-wide router, built from settings on first use."""
    global _router
    if _router is None:
        with _router_lock:
            if _router is None:
                settings = get_settings()
                _router = AlertRouter(
                    get_alert_sender(),
                    min_severity=settings.alert_min_severity,
                    suppression_window_seconds=settings.alert_suppression_seconds,
                )
    return _router


def set_alert_router(router: AlertRouter | None) -> None:
    """Swap the process-wide router. Intended for tests and for wiring in a
    different transport at startup without reaching into module state."""
    global _router
    _router = router


def notify(
    title: str,
    message: str,
    *,
    severity: Severity = Severity.WARNING,
    key: str | None = None,
    **context: object,
) -> bool:
    """Raise an alert. Never raises; returns whether it was delivered."""
    return get_alert_router().dispatch(
        Alert(
            title=title,
            message=message,
            severity=severity,
            key=key,
            context=dict(context),
        )
    )

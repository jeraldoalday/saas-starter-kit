from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AlertMessage:
    subject: str
    body: str
    severity: str = "error"


class AlertSender(Protocol):
    """Transport for on-call/monitoring alerts.

    Implementations must not raise on delivery failure - a down paging
    provider should not crash the health check or script that raised the
    alert in the first place. Log and move on.
    """

    def send(self, message: AlertMessage) -> bool:
        """Attempt delivery. Returns True if the transport accepted it."""
        ...

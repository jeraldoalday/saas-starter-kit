"""The alert vocabulary: what an alert is and what a transport must do."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol


class Severity(StrEnum):
    """How much attention an alert deserves, least to most.

    Three levels on purpose. Anything finer becomes a taxonomy nobody
    agrees on at 3am, and the only decision this drives is whether a
    notification goes out at all.
    """

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


_RANK: dict[str, int] = {
    Severity.INFO.value: 0,
    Severity.WARNING.value: 1,
    Severity.CRITICAL.value: 2,
}


def severity_rank(severity: str) -> int:
    """Numeric rank; an unknown value sorts highest so it is never dropped.

    The opposite of the role ranking in app/models/role.py, and for the
    opposite reason: there, an unrecognised value must deny access; here, an
    unrecognised value must not silently swallow a page.
    """
    return _RANK.get(str(severity), max(_RANK.values()))


@dataclass(frozen=True)
class Alert:
    """One thing an operator may need to know about.

    `key` is what deduplication groups on: a database outage produces one
    alert per readiness probe, and an operator needs the first one, not one
    every ten seconds until they fix it.
    """

    title: str
    message: str
    severity: Severity = Severity.WARNING
    key: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def dedupe_key(self) -> str:
        return self.key or f"{self.severity.value}:{self.title}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "message": self.message,
            "severity": self.severity.value,
            "key": self.dedupe_key,
            "context": self.context,
            "occurred_at": self.occurred_at.isoformat(),
        }


class AlertSender(Protocol):
    """Transport for operational alerts.

    Implementations must not raise. An alert is a report about something
    already going wrong; a transport that throws turns one incident into
    two, and the second one takes down the request that was trying to tell
    you about the first.
    """

    def send(self, alert: Alert) -> bool:
        """Attempt delivery. Returns True if the transport accepted it."""
        ...

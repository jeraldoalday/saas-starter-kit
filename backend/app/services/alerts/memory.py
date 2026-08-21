from app.services.alerts.base import Alert, AlertSender


class InMemoryAlertSender(AlertSender):
    """Collects alerts in a list so tests can assert on what was raised."""

    def __init__(self) -> None:
        self.sent: list[Alert] = []

    def send(self, alert: Alert) -> bool:
        self.sent.append(alert)
        return True

    def clear(self) -> None:
        self.sent.clear()

    def titles(self) -> list[str]:
        return [alert.title for alert in self.sent]

    def last(self) -> Alert | None:
        return self.sent[-1] if self.sent else None

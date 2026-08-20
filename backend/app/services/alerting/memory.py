from app.services.alerting.base import AlertMessage


class InMemoryAlertSender:
    """Collects alerts in a list so tests can assert on what was sent."""

    def __init__(self) -> None:
        self.sent: list[AlertMessage] = []

    def send(self, message: AlertMessage) -> bool:
        self.sent.append(message)
        return True

    def clear(self) -> None:
        self.sent.clear()

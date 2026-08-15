from app.services.email.base import EmailMessage


class InMemoryEmailSender:
    """Collects messages in a list so tests can assert on what was sent."""

    def __init__(self) -> None:
        self.outbox: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> bool:
        self.outbox.append(message)
        return True

    def clear(self) -> None:
        self.outbox.clear()

    def last_to(self, recipient: str) -> EmailMessage | None:
        for message in reversed(self.outbox):
            if message.to == recipient:
                return message
        return None

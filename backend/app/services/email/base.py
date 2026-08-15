from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text_body: str
    html_body: str | None = None


class EmailSender(Protocol):
    """Transport for transactional email.

    Implementations must not raise on delivery failure - a bounced
    verification email should not fail the request that triggered it. Log
    and move on; the user can request another.
    """

    def send(self, message: EmailMessage) -> bool:
        """Attempt delivery. Returns True if the transport accepted it."""
        ...

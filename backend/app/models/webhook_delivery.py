import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.webhook_endpoint import WebhookEventType

if TYPE_CHECKING:
    from app.models.webhook_endpoint import WebhookEndpoint

# Matches app.models.usage_event.MAX_EVENT_NAME_LENGTH: the same names go in
# both places when an event is worth both measuring and announcing.
MAX_EVENT_TYPE_LENGTH = 64

# The tail of whatever the receiver said, or of the transport error. Enough
# to recognise "connection refused" or an HTML error page's first line;
# short enough that a misconfigured receiver returning a megabyte of markup
# cannot fill the table with it.
MAX_ERROR_LENGTH = 255


class DeliveryStatus(StrEnum):
    """Where one delivery is in its life.

    Three states rather than a pair of booleans, because the interesting
    question is which of them a row is *in* — "queued, still retrying" and
    "given up" are the two an operator triages differently, and a
    `delivered` boolean plus an `abandoned` boolean can represent neither
    without the other being read alongside it.
    """

    #: Queued, and due at `next_attempt_at`. Both "never tried" and
    #: "tried, failed, waiting to retry" — the difference is `attempts`,
    #: which the worker needs anyway and a status would duplicate.
    PENDING = "pending"
    DELIVERED = "delivered"
    #: Out of attempts. Terminal: nothing retries these, and they stay for
    #: the retention window so "why did my handler never run" is answerable.
    FAILED = "failed"


class WebhookDelivery(Base):
    """One event, queued for one endpoint, with its attempt history.

    A row per (event, endpoint) pair rather than a row per event: two
    endpoints subscribed to the same event fail independently, retry on
    independent schedules, and are disabled independently, so sharing a row
    between them would mean the slower receiver's backoff governed the
    faster one's.

    The queue is a table rather than a broker, and that is a deliberate
    scaffold-shaped choice. A broker is the right answer at volume, and the
    wrong first dependency for a product that has not shipped: this needs no
    new infrastructure, survives a restart because the rows are already
    durable, and is inspectable with the same SQL as everything else. What
    it costs is a polling worker (`app/scripts/deliver_webhooks.py`) rather
    than a push, which at this size is a latency of seconds.
    """

    __tablename__ = "webhook_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(
        String(MAX_EVENT_TYPE_LENGTH), nullable=False
    )

    # The body, stored as the object rather than as the serialised string.
    # The exact bytes are re-derived at send time by the canonical
    # serialiser in webhook_service, so that the signature and the body
    # cannot disagree about key order — a mismatch there is invisible here
    # and fails verification on the customer's side.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DeliveryStatus.PENDING.value
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # When this row is next eligible to be sent. Set on creation to "now" so
    # a fresh delivery is due immediately, and pushed out by the backoff
    # schedule after each failure. Nullable only for terminal rows, where
    # "next" is meaningless.
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # What the receiver said last. Null when the attempt never got a
    # response at all (DNS failure, refused connection, timeout), which is
    # itself the useful distinction: a 500 means the handler ran and threw,
    # no status at all means it was never reached.
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(
        String(MAX_ERROR_LENGTH), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    endpoint: Mapped["WebhookEndpoint"] = relationship(back_populates="deliveries")

    __table_args__ = (
        # The worker's claim query: everything pending and due, oldest
        # first. Leading with status keeps the scan off the delivered rows,
        # which are the overwhelming majority of the table on a healthy
        # deployment.
        Index("ix_webhook_deliveries_status_next_attempt", "status", "next_attempt_at"),
        # "What happened to this endpoint lately" — the owner-facing log.
        Index(
            "ix_webhook_deliveries_endpoint_id_created_at", "endpoint_id", "created_at"
        ),
        # The retention prune's cutoff scan.
        Index("ix_webhook_deliveries_created_at", "created_at"),
    )

    @property
    def is_terminal(self) -> bool:
        """Whether nothing further will happen to this row."""
        return self.status in (DeliveryStatus.DELIVERED.value, DeliveryStatus.FAILED.value)

    @property
    def is_ping(self) -> bool:
        return self.event_type == WebhookEventType.PING.value

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<WebhookDelivery {self.event_type} endpoint={self.endpoint_id} "
            f"status={self.status} attempts={self.attempts}>"
        )

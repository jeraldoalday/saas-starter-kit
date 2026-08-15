from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ProcessedWebhookEvent(Base):
    """A Stripe event id we've already applied.

    Stripe redelivers events until it gets a 2xx, and may deliver the same
    event more than once regardless. Recording ids lets replays be dropped
    instead of double-applying a state change.
    """

    __tablename__ = "processed_webhook_events"

    # Stripe's event id (evt_...) is the natural key.
    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

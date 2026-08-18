import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Event names are free-form strings so a product team can add one without a
# migration, but they are bounded and validated at the API edge (see
# app/schemas/analytics.py) to keep the cardinality of the name column low.
MAX_EVENT_NAME_LENGTH = 64


class EventName(StrEnum):
    """Names emitted by the backend itself.

    Frontend events use their own names; these are the ones the server
    records so the funnel is measurable even when the SPA never loads (a
    signup by API client, a webhook-driven subscription change).
    """

    USER_REGISTERED = "user.registered"
    USER_LOGGED_IN = "user.logged_in"
    USER_VERIFIED = "user.verified"
    CHECKOUT_STARTED = "billing.checkout_started"
    PORTAL_OPENED = "billing.portal_opened"
    SUBSCRIPTION_ACTIVATED = "billing.subscription_activated"
    SUBSCRIPTION_CANCELED = "billing.subscription_canceled"
    SUBSCRIPTION_PAST_DUE = "billing.subscription_past_due"
    ACCOUNT_DELETED = "account.deleted"
    ACCOUNT_DEACTIVATED = "account.deactivated"
    ACCOUNT_REACTIVATED = "account.reactivated"


class EventSource(StrEnum):
    BACKEND = "backend"
    FRONTEND = "frontend"


class UsageEvent(Base):
    """One recorded product event.

    Deliberately a plain append-only table rather than a third-party
    analytics SDK: the events a SaaS bills and reasons about (signups,
    activations, subscription changes) belong in the same database as the
    rows they describe, so they can be joined and so they leave with the
    account when it is deleted.
    """

    __tablename__ = "usage_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Nullable: pre-signup events (landing page views) have no account yet.
    # ON DELETE CASCADE, so erasing an account erases its event history too.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(MAX_EVENT_NAME_LENGTH), nullable=False)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=EventSource.BACKEND.value
    )
    # Small, denormalised context (plan id, referrer). Not indexed: this is
    # for reading back alongside an event, not for querying across them.
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        # The two access patterns: "this account's recent activity" and
        # "how often did X happen over a window".
        Index("ix_usage_events_user_id_occurred_at", "user_id", "occurred_at"),
        Index("ix_usage_events_name_occurred_at", "name", "occurred_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<UsageEvent {self.name} user={self.user_id} at={self.occurred_at}>"

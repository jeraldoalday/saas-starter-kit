import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.webhook_delivery import WebhookDelivery

# A URL long enough for a signed callback with query parameters, bounded so
# one row cannot carry a payload's worth of text in a column that is read on
# every dispatch.
MAX_URL_LENGTH = 2048

# A label the owner chooses ("staging relay", "Zapier"). Rendered straight
# back into the dashboard, so it is bounded for the same reason an API key's
# name is.
MAX_DESCRIPTION_LENGTH = 120

# Long enough to hold "whsec_" plus 32 url-safe bytes.
MAX_SECRET_LENGTH = 64

# Why an endpoint was switched off, in words the owner can act on. Short:
# it is a reason, not a stack trace.
MAX_DISABLED_REASON_LENGTH = 255


class WebhookEventType(StrEnum):
    """The events an endpoint can subscribe to.

    A closed enum rather than the free-form names `usage_events` uses, and
    for the audit trail's reason rather than the analytics one: these
    strings are a published contract. A customer writes `if event ==
    "api_key.created"` in code we do not control, so a typo here is not a
    row somebody fails to find later — it is a handler on the other side of
    the internet that silently never runs.

    The names deliberately match the `EventName` spellings the analytics
    table already uses where the two describe the same moment. Two names for
    one event would mean a customer correlating their own logs against a
    support answer finds neither.
    """

    #: Sent by the "send a test event" button and by nothing else. Every
    #: endpoint receives it regardless of what it subscribed to, because an
    #: endpoint that cannot be tested until it subscribes to something real
    #: can only be debugged in production.
    PING = "ping"

    API_KEY_CREATED = "api_key.created"
    API_KEY_REVOKED = "api_key.revoked"

    SUBSCRIPTION_ACTIVATED = "billing.subscription_activated"
    SUBSCRIPTION_CANCELED = "billing.subscription_canceled"
    SUBSCRIPTION_PAST_DUE = "billing.subscription_past_due"


#: The event types a caller may subscribe to, as plain strings. PING is
#: excluded: it is delivered to every endpoint anyway, so letting it be
#: subscribed to would imply endpoints exist that do *not* receive it.
SUBSCRIBABLE_EVENT_TYPES: tuple[str, ...] = tuple(
    event.value for event in WebhookEventType if event is not WebhookEventType.PING
)


class WebhookEndpoint(Base):
    """One URL an account has asked us to notify.

    The secret is stored in a form the server can read back, which is the
    opposite of the bargain :class:`app.models.api_key.ApiKey` strikes — and
    deliberately so, because the two secrets point in opposite directions. An
    API key authenticates *the customer to us*, so we only ever need to
    recognise one and a digest suffices. A webhook secret authenticates *us
    to the customer*: every delivery has to be signed with it, which means
    the plaintext has to be available at send time. There is no hashing
    scheme that allows that.

    What follows from it is that this column is as sensitive as a password
    column, without being one: it grants nothing on this system, only the
    ability to forge a request to the customer's. It is why the secret is
    shown in the dashboard rather than hidden — hiding a value the server
    must keep in readable form buys no security and costs the owner the
    ability to configure their receiver — and why rotation exists.
    """

    __tablename__ = "webhook_endpoints"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    url: Mapped[str] = mapped_column(String(MAX_URL_LENGTH), nullable=False)
    description: Mapped[str] = mapped_column(
        String(MAX_DESCRIPTION_LENGTH), nullable=False, default=""
    )
    secret: Mapped[str] = mapped_column(String(MAX_SECRET_LENGTH), nullable=False)

    # The subscription list, as a JSON array of event-type strings. A JSON
    # column rather than a join table because it is read whole on every
    # dispatch and never queried into: "which endpoints want this event" is
    # answered by loading an account's handful of endpoints and filtering in
    # Python, which is one indexed query against a table a customer cannot
    # grow past their plan's ceiling.
    event_types: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=list
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    # Set when delivery gave up on this endpoint. Kept alongside `is_active`
    # rather than replacing it, so "switched off by the owner" and "switched
    # off because it had been failing for a day" are distinguishable — the
    # second wants an explanation in the interface, the first does not.
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    disabled_reason: Mapped[str | None] = mapped_column(
        String(MAX_DISABLED_REASON_LENGTH), nullable=True
    )

    # Reset by any delivery that succeeds. This is the counter auto-disable
    # watches, and counting *consecutive* failures rather than total ones is
    # what keeps a receiver that drops one request in a thousand from being
    # switched off after a busy month.
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_failure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="webhook_endpoints")
    # Deliveries belong to their endpoint: removing the endpoint removes the
    # queue of things nobody will ever deliver to it.
    deliveries: Mapped[list["WebhookDelivery"]] = relationship(
        back_populates="endpoint",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        # The listing query: this account's endpoints, newest first.
        Index("ix_webhook_endpoints_user_id_created_at", "user_id", "created_at"),
    )

    @property
    def is_deliverable(self) -> bool:
        """Whether dispatch should queue anything for this endpoint.

        One property rather than two checks at each call site, because
        "active" and "not auto-disabled" have to be asked together and
        forgetting the second is how a dead endpoint keeps accruing a queue.
        """
        return self.is_active and self.disabled_at is None

    def subscribes_to(self, event_type: str) -> bool:
        """Whether this endpoint wants `event_type`.

        PING is always wanted: the test button has to work before the
        endpoint is subscribed to anything, which is exactly when someone is
        trying to find out whether their receiver is wired up correctly.
        """
        if event_type == WebhookEventType.PING.value:
            return True
        return event_type in self.event_types

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<WebhookEndpoint {self.url} user={self.user_id} "
            f"active={self.is_deliverable}>"
        )

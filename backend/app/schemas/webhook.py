"""Wire shapes for outbound webhooks.

The one shape worth explaining is the split between `WebhookEndpointRead`
and `WebhookEndpointSecret`. The signing secret is stored readable — it has
to be, since every delivery is signed with it — but that is not a reason to
put it in every response that mentions an endpoint. It is returned when it
is created, when it is rotated, and from one endpoint whose entire job is to
reveal it, and that last request is recorded in the audit trail.

The listing therefore carries no secrets at all, which means the common case
— a dashboard polling a list of endpoints — never moves them through a proxy
log or a browser's memory. `ApiKeyCreated` makes the same separation for the
same reason; the difference is only that a key's secret is genuinely
unrecoverable afterwards and this one is deliberately not.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.webhook_delivery import DeliveryStatus
from app.models.webhook_endpoint import (
    MAX_DESCRIPTION_LENGTH,
    MAX_URL_LENGTH,
    SUBSCRIBABLE_EVENT_TYPES,
)


def _validate_event_types(value: list[str]) -> list[str]:
    """Reject unknown event names, naming the offender.

    Rejected at the edge rather than silently dropped, unlike the
    normalisation the service does. A developer who typed
    `"api_key.create"` wants to be told; the service's quiet filtering is
    the backstop for callers inside the codebase, not the error message a
    human reads.
    """
    unknown = sorted(set(value) - set(SUBSCRIBABLE_EVENT_TYPES))
    if unknown:
        raise ValueError(
            f"Unknown event type(s): {', '.join(unknown)}. "
            f"Valid types: {', '.join(SUBSCRIBABLE_EVENT_TYPES)}"
        )
    return sorted(set(value))


class WebhookEndpointCreate(BaseModel):
    """Request to register a URL.

    `event_types` may be empty, and an endpoint subscribed to nothing is a
    legitimate state rather than a mistake to reject: it still receives the
    test ping, which is exactly how somebody verifies a receiver before
    deciding what to send it.
    """

    url: str = Field(min_length=1, max_length=MAX_URL_LENGTH)
    description: str = Field(default="", max_length=MAX_DESCRIPTION_LENGTH)
    event_types: list[str] = Field(default_factory=list)

    @field_validator("event_types")
    @classmethod
    def _known_event_types(cls, value: list[str]) -> list[str]:
        return _validate_event_types(value)


class WebhookEndpointUpdate(BaseModel):
    """Change a label, a subscription list, or whether an endpoint is on.

    Every field is optional and None means "leave it alone" — the URL is
    absent entirely because it is not editable, for the reason
    `endpoints.update_endpoint` gives: moving a URL under a secret the old
    receiver already holds is not an edit, it is a new endpoint wearing the
    old one's history.
    """

    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)
    event_types: list[str] | None = None
    is_active: bool | None = None

    @field_validator("event_types")
    @classmethod
    def _known_event_types(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _validate_event_types(value)


class WebhookEndpointRead(BaseModel):
    """An endpoint as it is listed: everything except the secret."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    description: str
    event_types: list[str]
    is_active: bool
    created_at: datetime
    disabled_at: datetime | None
    disabled_reason: str | None
    consecutive_failures: int
    last_success_at: datetime | None
    last_failure_at: datetime | None
    # Derived from the two switches together, so a client never has to know
    # that "off" is two columns.
    is_deliverable: bool


class WebhookEndpointSecret(BaseModel):
    """The signing secret, on its own.

    Returned by creation, by rotation, and by the reveal endpoint. Its own
    model rather than a field on the read shape so that adding it to a
    listing response would have to be a deliberate edit.
    """

    id: uuid.UUID
    secret: str = Field(
        description=(
            "The HMAC signing secret. Configure it in your receiver; every "
            "delivery is signed with it."
        )
    )


class WebhookEndpointCreated(WebhookEndpointRead):
    """The creation response: the endpoint, plus its secret once."""

    secret: str = Field(description="The HMAC signing secret for this endpoint.")


class WebhookDeliveryRead(BaseModel):
    """One delivery attempt history, as the owner's log shows it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    endpoint_id: uuid.UUID
    event_type: str
    status: DeliveryStatus
    attempts: int
    next_attempt_at: datetime | None
    last_status_code: int | None
    last_error: str | None
    created_at: datetime
    delivered_at: datetime | None


class WebhookEventTypeRead(BaseModel):
    """One subscribable event, with the words the dashboard shows for it."""

    event_type: str
    label: str
    description: str


__all__ = [
    "WebhookDeliveryRead",
    "WebhookEndpointCreate",
    "WebhookEndpointCreated",
    "WebhookEndpointRead",
    "WebhookEndpointSecret",
    "WebhookEndpointUpdate",
    "WebhookEventTypeRead",
]

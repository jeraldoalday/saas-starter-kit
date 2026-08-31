"""What each subscribable event means, in words a customer can act on.

Served by `GET /webhooks/event-types` so the dashboard's checkbox list and
the buyer's integration docs come from one place. It is here rather than on
the model because it is product copy — the model should not know how an
event is described in a settings panel — and it is served rather than
hardcoded in the SPA because a customer integrating over the API needs the
same list without reading our frontend.

Every entry is keyed by a `WebhookEventType`, and the test suite asserts the
two stay in step: an event that can be subscribed to and has no description
is a checkbox with no label.
"""

from dataclasses import dataclass

from app.models.webhook_endpoint import SUBSCRIBABLE_EVENT_TYPES, WebhookEventType


@dataclass(frozen=True)
class EventTypeInfo:
    """One event, its short label, and what causes it."""

    event_type: str
    label: str
    description: str


EVENT_TYPES: tuple[EventTypeInfo, ...] = (
    EventTypeInfo(
        event_type=WebhookEventType.API_KEY_CREATED.value,
        label="API key created",
        description=(
            "A new API key was minted for the account. The payload carries "
            "the key's id and prefix, never its secret."
        ),
    ),
    EventTypeInfo(
        event_type=WebhookEventType.API_KEY_REVOKED.value,
        label="API key revoked",
        description=(
            "An API key stopped working. Worth handling if your system "
            "caches credentials: this is the moment to drop one."
        ),
    ),
    EventTypeInfo(
        event_type=WebhookEventType.SUBSCRIPTION_ACTIVATED.value,
        label="Subscription activated",
        description=(
            "A subscription started or resumed, and the account is on the "
            "paid plan's ceilings from this moment."
        ),
    ),
    EventTypeInfo(
        event_type=WebhookEventType.SUBSCRIPTION_CANCELED.value,
        label="Subscription canceled",
        description=(
            "A subscription ended. The account falls back to the free "
            "tier's ceilings immediately."
        ),
    ),
    EventTypeInfo(
        event_type=WebhookEventType.SUBSCRIPTION_PAST_DUE.value,
        label="Payment past due",
        description=(
            "A renewal payment failed. The account keeps its data and loses "
            "the paid ceilings until the payment succeeds."
        ),
    ),
)

#: By event type, for the lookups the API does. Built rather than written
#: out twice, so the tuple above stays the single ordered source.
EVENT_TYPES_BY_NAME: dict[str, EventTypeInfo] = {
    info.event_type: info for info in EVENT_TYPES
}


def describe(event_type: str) -> EventTypeInfo | None:
    """The catalog entry for `event_type`, or None if it has no entry.

    None rather than a raise: the caller asking is rendering a log row that
    may name an event from a build that has since removed it, and a missing
    label is a cell that falls back to the raw name rather than a page that
    fails to load.
    """
    return EVENT_TYPES_BY_NAME.get(event_type)


def missing_descriptions() -> tuple[str, ...]:
    """Subscribable events with no catalog entry. Asserted empty by tests."""
    return tuple(
        event_type
        for event_type in SUBSCRIBABLE_EVENT_TYPES
        if event_type not in EVENT_TYPES_BY_NAME
    )


__all__ = [
    "EVENT_TYPES",
    "EVENT_TYPES_BY_NAME",
    "EventTypeInfo",
    "describe",
    "missing_descriptions",
]

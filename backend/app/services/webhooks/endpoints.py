"""The registry of URLs an account has asked us to notify.

Create, list, rotate, switch off, delete. Nothing here talks to the network
— an endpoint is a row until the delivery worker picks something up for it.

The one design decision worth arguing with is that deleting an endpoint
really deletes it, where a revoked API key is kept as a tombstone. The
asymmetry is on purpose: a key's tombstone answers "when did this credential
stop working", which is a security question somebody asks after an incident.
An endpoint grants nothing and authenticates nobody, so the equivalent
question ("when did we stop sending to this URL?") is answered by the audit
trail, which records the deletion and outlives the row. Keeping a dead
endpoint listed would leave the owner deciding, every time they look, which
of the URLs in front of them are live.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.webhook_delivery import WebhookDelivery
from app.models.webhook_endpoint import (
    MAX_DESCRIPTION_LENGTH,
    MAX_DISABLED_REASON_LENGTH,
    SUBSCRIBABLE_EVENT_TYPES,
    WebhookEndpoint,
)
from app.services.webhooks.signing import generate_secret
from app.services.webhooks.urls import validate_webhook_url

logger = logging.getLogger("app.webhooks")

# The hard ceiling per account, the same shape and for the same reason as
# `api_key_service.MAX_ACTIVE_KEYS_PER_USER`: the plan carries the product's
# answer, this carries the deployment's. Every endpoint subscribed to an
# event multiplies the rows one event writes, so this bounds the fan-out a
# single account can create, not just the length of a list.
MAX_ENDPOINTS_PER_USER = 10

# A page of delivery history. Matches the audit trail's page size, because
# it is read in the same way and by the same person.
DEFAULT_DELIVERY_PAGE_SIZE = 50
MAX_DELIVERY_PAGE_SIZE = 200


class EndpointLimitExceededError(Exception):
    """The account already holds as many endpoints as it is allowed."""


class EndpointNotFoundError(Exception):
    """No endpoint with that id belongs to this account."""


def _now() -> datetime:
    return datetime.now(UTC)


def normalise_event_types(event_types: list[str] | None) -> list[str]:
    """Deduplicate, drop unknown names, and return a stable order.

    Unknown names are dropped rather than rejected, and only here — the
    request schema rejects them at the edge with a message naming the
    offender, which is the error a developer can act on. By the time a list
    reaches this function it has either been through that schema or come
    from a caller inside the codebase, and silently carrying a name nothing
    will ever emit is worse than quietly discarding it.

    Sorted so that two endpoints subscribed to the same events compare equal
    in the dashboard regardless of the order they were typed in.
    """
    if not event_types:
        return []
    wanted = {name for name in event_types if name in SUBSCRIBABLE_EVENT_TYPES}
    return sorted(wanted)


def _endpoints_for(db: Session, user: User) -> list[WebhookEndpoint]:
    stmt = (
        select(WebhookEndpoint)
        .where(WebhookEndpoint.user_id == user.id)
        .order_by(WebhookEndpoint.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def list_endpoints(db: Session, user: User) -> list[WebhookEndpoint]:
    """This account's endpoints, newest first, disabled ones included."""
    return _endpoints_for(db, user)


def active_endpoint_count(db: Session, user: User) -> int:
    """How many of this account's endpoints would receive an event now.

    Counted in Python rather than SQL for the same reason
    `api_key_service.active_key_count` is: "deliverable" folds two columns
    together, and the model already knows how to answer it.
    """
    return sum(1 for endpoint in _endpoints_for(db, user) if endpoint.is_deliverable)


def effective_endpoint_limit(max_endpoints: int | None) -> int:
    """The ceiling that actually binds, given a plan's asking price.

    Identical in shape to `api_key_service.effective_key_limit`, and the
    duplication is deliberate rather than abstracted away: the two clamps
    happen to share arithmetic today, and a shared helper would make the
    next plan-specific rule ("unlimited really means unlimited for
    endpoints") a change to a function two features depend on.
    """
    if max_endpoints is None:
        return MAX_ENDPOINTS_PER_USER
    return max(min(max_endpoints, MAX_ENDPOINTS_PER_USER), 0)


def get_endpoint(db: Session, user: User, endpoint_id: uuid.UUID) -> WebhookEndpoint:
    """One of this account's endpoints, or raise `EndpointNotFoundError`.

    Scoped to the owner in the query rather than fetched and then checked:
    the check that is part of the WHERE clause is the one that cannot be
    forgotten by the next caller.
    """
    stmt = select(WebhookEndpoint).where(
        WebhookEndpoint.id == endpoint_id, WebhookEndpoint.user_id == user.id
    )
    endpoint = db.execute(stmt).scalar_one_or_none()
    if endpoint is None:
        raise EndpointNotFoundError(str(endpoint_id))
    return endpoint


def create_endpoint(
    db: Session,
    user: User,
    *,
    url: str,
    description: str = "",
    event_types: list[str] | None = None,
    max_endpoints: int | None = None,
    allow_insecure: bool = False,
    now: datetime | None = None,
) -> WebhookEndpoint:
    """Register a URL, minting its signing secret.

    The URL is validated here as well as at the schema, and the repetition
    is the point: this is the function that writes the row, so a second call
    site added later inherits the SSRF check rather than bypassing it. The
    same argument `api_key_service.create_api_key` makes about its own
    ceiling.
    """
    now = now or _now()

    limit = effective_endpoint_limit(max_endpoints)
    if active_endpoint_count(db, user) >= limit:
        raise EndpointLimitExceededError(f"At most {limit} webhook endpoints per account")

    endpoint = WebhookEndpoint(
        user_id=user.id,
        url=validate_webhook_url(url, allow_insecure=allow_insecure),
        description=description.strip()[:MAX_DESCRIPTION_LENGTH],
        secret=generate_secret(),
        event_types=normalise_event_types(event_types),
        is_active=True,
        created_at=now,
    )
    db.add(endpoint)
    db.commit()
    db.refresh(endpoint)

    # The URL, never the secret. The URL is the thing an operator needs to
    # recognise the endpoint by when reading logs; the secret would be a
    # credential written to a file with a different retention policy from
    # the table it lives in.
    logger.info(
        "webhook endpoint created",
        extra={"webhook_endpoint_id": str(endpoint.id), "webhook_url": endpoint.url},
    )
    return endpoint


def update_endpoint(
    db: Session,
    user: User,
    endpoint_id: uuid.UUID,
    *,
    description: str | None = None,
    event_types: list[str] | None = None,
) -> WebhookEndpoint:
    """Change an endpoint's label or its subscription list.

    The URL is deliberately not editable. Changing it in place would move a
    secret that a receiver already holds to a host that never agreed to it,
    and would make the audit trail's record of what was registered stop
    matching what is registered. Deleting and re-adding is one more click
    and leaves an honest history.
    """
    endpoint = get_endpoint(db, user, endpoint_id)

    if description is not None:
        endpoint.description = description.strip()[:MAX_DESCRIPTION_LENGTH]
    if event_types is not None:
        endpoint.event_types = normalise_event_types(event_types)

    db.commit()
    db.refresh(endpoint)
    return endpoint


def rotate_secret(
    db: Session, user: User, endpoint_id: uuid.UUID
) -> WebhookEndpoint:
    """Replace an endpoint's signing secret.

    Immediate and without an overlap window, which is a real trade. An
    overlap — sign with both for an hour — would let a receiver update at
    its own pace, and is why `parse_signature_header` returns every `v1=`
    part rather than the first. It is not implemented here because the
    honest version needs a second secret column and an expiry to sweep, and
    a half-implemented overlap that silently never ends is worse than none:
    the whole point of rotating is that the old secret stops working.

    So the contract is the blunt one, and the UI says so: deliveries signed
    with the new secret start immediately, and a receiver still checking the
    old one will reject them until it is updated.
    """
    endpoint = get_endpoint(db, user, endpoint_id)
    endpoint.secret = generate_secret()
    db.commit()
    db.refresh(endpoint)

    logger.info(
        "webhook signing secret rotated",
        extra={"webhook_endpoint_id": str(endpoint.id)},
    )
    return endpoint


def set_endpoint_active(
    db: Session,
    user: User,
    endpoint_id: uuid.UUID,
    *,
    active: bool,
) -> WebhookEndpoint:
    """Switch an endpoint on or off by hand.

    Switching one back on clears the auto-disable state as well, which is
    what makes "we turned it off because it had been failing for a day" a
    recoverable condition rather than a reason to delete and re-register
    (and so to re-key the receiver). The failure counter resets with it:
    leaving it at its old value would auto-disable the endpoint again on the
    first failure after the fix.
    """
    endpoint = get_endpoint(db, user, endpoint_id)
    endpoint.is_active = active

    if active:
        endpoint.disabled_at = None
        endpoint.disabled_reason = None
        endpoint.consecutive_failures = 0

    db.commit()
    db.refresh(endpoint)
    return endpoint


def disable_endpoint(
    db: Session,
    endpoint: WebhookEndpoint,
    *,
    reason: str,
    now: datetime | None = None,
) -> WebhookEndpoint:
    """Switch an endpoint off because delivery gave up on it.

    Takes the row rather than an owner and an id, because the caller is the
    delivery worker: it holds the endpoint already, and it is not acting on
    behalf of a signed-in user whose ownership could be checked.

    `is_active` is left alone. The owner's switch and the system's switch
    are separate columns so that turning it back on is the owner's decision
    and does not depend on which of the two switched it off.
    """
    endpoint.disabled_at = now or _now()
    endpoint.disabled_reason = reason[:MAX_DISABLED_REASON_LENGTH]
    db.commit()

    logger.warning(
        "webhook endpoint auto-disabled",
        extra={
            "webhook_endpoint_id": str(endpoint.id),
            "webhook_url": endpoint.url,
            "reason": endpoint.disabled_reason,
        },
    )
    return endpoint


def delete_endpoint(db: Session, user: User, endpoint_id: uuid.UUID) -> None:
    """Remove an endpoint and everything queued for it.

    The queued deliveries go with it by cascade, and that is the intended
    reading of the action: "stop sending to this URL" has to include the
    things already waiting to be sent, or the owner watches events arrive at
    a URL they deleted for however long the backoff schedule runs.
    """
    endpoint = get_endpoint(db, user, endpoint_id)
    db.delete(endpoint)
    db.commit()

    logger.info("webhook endpoint deleted", extra={"webhook_endpoint_id": str(endpoint_id)})


def clamp_delivery_page_size(limit: int) -> int:
    return max(1, min(limit, MAX_DELIVERY_PAGE_SIZE))


def list_deliveries(
    db: Session,
    user: User,
    *,
    endpoint_id: uuid.UUID | None = None,
    limit: int = DEFAULT_DELIVERY_PAGE_SIZE,
    offset: int = 0,
) -> list[WebhookDelivery]:
    """Recent deliveries for this account, newest first.

    Joined to the endpoint table rather than filtered by a user column on
    the delivery row. A `user_id` on the delivery would denormalise an
    answer that is already one hop away and would then have to be kept in
    step; the join is indexed on both sides.
    """
    stmt = (
        select(WebhookDelivery)
        .join(WebhookEndpoint, WebhookDelivery.endpoint_id == WebhookEndpoint.id)
        .where(WebhookEndpoint.user_id == user.id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(clamp_delivery_page_size(limit))
        .offset(max(offset, 0))
    )
    if endpoint_id is not None:
        stmt = stmt.where(WebhookDelivery.endpoint_id == endpoint_id)

    return list(db.execute(stmt).scalars().all())


def count_endpoints(db: Session) -> int:
    """Total endpoints across all accounts — for the admin overview."""
    return int(
        db.execute(select(func.count()).select_from(WebhookEndpoint)).scalar_one()
    )


__all__ = [
    "DEFAULT_DELIVERY_PAGE_SIZE",
    "MAX_DELIVERY_PAGE_SIZE",
    "MAX_ENDPOINTS_PER_USER",
    "EndpointLimitExceededError",
    "EndpointNotFoundError",
    "active_endpoint_count",
    "clamp_delivery_page_size",
    "count_endpoints",
    "create_endpoint",
    "delete_endpoint",
    "disable_endpoint",
    "effective_endpoint_limit",
    "get_endpoint",
    "list_deliveries",
    "list_endpoints",
    "normalise_event_types",
    "rotate_secret",
    "set_endpoint_active",
    "update_endpoint",
]

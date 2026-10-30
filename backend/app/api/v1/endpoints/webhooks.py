import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps_rate_limit import (
    webhook_endpoint_create_rate_limit,
    webhook_ping_rate_limit,
    webhook_secret_rate_limit,
)
from app.api.request_info import request_origin
from app.core.config import get_settings
from app.database import get_db
from app.deps import get_current_user
from app.models.audit_log import AuditAction
from app.models.usage_event import EventName
from app.models.user import User
from app.models.webhook_endpoint import WebhookEventType
from app.schemas.webhook import (
    WebhookDeliveryRead,
    WebhookEndpointCreate,
    WebhookEndpointCreated,
    WebhookEndpointRead,
    WebhookEndpointSecret,
    WebhookEndpointUpdate,
    WebhookEventTypeRead,
)
from app.services.analytics_service import record_event
from app.services.audit_service import record as record_audit
from app.services.entitlement_service import (
    QuotaExceededError,
    check_webhook_endpoint_quota,
    entitlements_for,
)
from app.services.webhooks.catalog import EVENT_TYPES
from app.services.webhooks.dispatch import queue_delivery
from app.services.webhooks.endpoints import (
    DEFAULT_DELIVERY_PAGE_SIZE,
    MAX_DELIVERY_PAGE_SIZE,
    EndpointLimitExceededError,
    EndpointNotFoundError,
    create_endpoint,
    delete_endpoint,
    get_endpoint,
    list_deliveries,
    list_endpoints,
    rotate_secret,
    set_endpoint_active,
    update_endpoint,
)
from app.services.webhooks.urls import UnsafeWebhookUrlError

# Session-gated throughout, like the API key routes and for a related
# reason. A leaked API key can read and write the product; it must not be
# able to point a new webhook endpoint at a server the attacker controls and
# have this account's events delivered there, nor to read the signing secret
# that would let it forge deliveries to the real receiver. Both of those are
# exfiltration primitives that survive the key being revoked.
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _not_found() -> HTTPException:
    # 404 whether the endpoint belongs to someone else or does not exist:
    # confirming the former would leak other accounts' endpoint ids.
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Webhook endpoint not found"
    )


@router.get("/event-types", response_model=list[WebhookEventTypeRead])
def read_event_types() -> list[WebhookEventTypeRead]:
    """Every event an endpoint can subscribe to, with what causes it.

    Unauthenticated — deliberately. It is the same list that belongs in the
    public integration docs, it says nothing about any account, and a
    developer evaluating the product should be able to read it before they
    have credentials.
    """
    return [
        WebhookEventTypeRead(
            event_type=info.event_type, label=info.label, description=info.description
        )
        for info in EVENT_TYPES
    ]


@router.get("/endpoints", response_model=list[WebhookEndpointRead])
def list_webhook_endpoints(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[WebhookEndpointRead]:
    """This account's endpoints, newest first. No secrets in the response."""
    return [
        WebhookEndpointRead.model_validate(endpoint)
        for endpoint in list_endpoints(db, current_user)
    ]


@router.post(
    "/endpoints",
    response_model=WebhookEndpointCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(webhook_endpoint_create_rate_limit)],
)
def create_webhook_endpoint(
    data: WebhookEndpointCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WebhookEndpointCreated:
    """Register a URL and mint its signing secret.

    The secret comes back in this response so a receiver can be configured
    in one pass. Unlike an API key it is not the *only* time — it can be
    revealed again — because the server keeps it readable regardless and
    pretending otherwise would only cost the owner a rotation every time
    they redeployed their receiver.
    """
    entitlements = entitlements_for(current_user)
    try:
        check_webhook_endpoint_quota(db, current_user, entitlements=entitlements)
    except QuotaExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{exc.quota}. Delete an endpoint you no longer use, or "
                f"upgrade your plan for more."
            ),
        ) from exc

    try:
        endpoint = create_endpoint(
            db,
            current_user,
            url=data.url,
            description=data.description,
            event_types=data.event_types,
            max_endpoints=entitlements.max_webhook_endpoints,
            allow_insecure=get_settings().webhook_allow_insecure_urls,
        )
    except UnsafeWebhookUrlError as exc:
        # 422 rather than 400: the URL is a field of the request body that
        # failed validation, which is the same shape of failure Pydantic
        # reports for the fields it checks itself. Spelled
        # UNPROCESSABLE_CONTENT because ..._ENTITY is the deprecated alias.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except EndpointLimitExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    record_event(
        db,
        EventName.WEBHOOK_ENDPOINT_CREATED,
        user=current_user,
        properties={
            "event_types": len(endpoint.event_types),
            "plan": entitlements.plan_key,
        },
    )
    # The URL and never the secret, matching what the API key trail records:
    # enough to say which endpoint appeared, useless to whoever reads the
    # trail later.
    record_audit(
        db,
        AuditAction.WEBHOOK_ENDPOINT_CREATED,
        user=current_user,
        context={
            "endpoint_id": str(endpoint.id),
            "url": endpoint.url,
            "event_types": endpoint.event_types,
        },
        **request_origin(request),
    )
    return WebhookEndpointCreated(
        **WebhookEndpointRead.model_validate(endpoint).model_dump(),
        secret=endpoint.secret,
    )


@router.get("/endpoints/{endpoint_id}", response_model=WebhookEndpointRead)
def read_webhook_endpoint(
    endpoint_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WebhookEndpointRead:
    try:
        return WebhookEndpointRead.model_validate(
            get_endpoint(db, current_user, endpoint_id)
        )
    except EndpointNotFoundError as exc:
        raise _not_found() from exc


@router.patch("/endpoints/{endpoint_id}", response_model=WebhookEndpointRead)
def update_webhook_endpoint(
    endpoint_id: uuid.UUID,
    data: WebhookEndpointUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WebhookEndpointRead:
    """Change a label, a subscription list, or whether an endpoint is on.

    Switching one back on is also how an auto-disabled endpoint is
    recovered — it clears the disable and resets the failure counter, so a
    fixed receiver is not switched off again by the count it accrued while
    it was broken.
    """
    try:
        if data.description is not None or data.event_types is not None:
            endpoint = update_endpoint(
                db,
                current_user,
                endpoint_id,
                description=data.description,
                event_types=data.event_types,
            )
        if data.is_active is not None:
            endpoint = set_endpoint_active(
                db, current_user, endpoint_id, active=data.is_active
            )
        if data.description is None and data.event_types is None and data.is_active is None:
            # An empty patch is not an error: it is idempotent and returns
            # the endpoint unchanged, which is what a client re-submitting a
            # form with nothing edited expects.
            endpoint = get_endpoint(db, current_user, endpoint_id)
    except EndpointNotFoundError as exc:
        raise _not_found() from exc

    # is_active is only in the context when this patch actually touched it.
    # Always including the endpoint's current value would show "Reactivated"
    # or "Deactivated" on the security page for an edit that only changed the
    # description or the subscription list, against an endpoint that already
    # happened to be off.
    context: dict[str, object] = {"endpoint_id": str(endpoint_id)}
    if data.event_types is not None:
        context["event_types"] = endpoint.event_types
    if data.is_active is not None:
        context["is_active"] = endpoint.is_active

    record_audit(
        db,
        AuditAction.WEBHOOK_ENDPOINT_UPDATED,
        user=current_user,
        context=context,
        **request_origin(request),
    )
    return WebhookEndpointRead.model_validate(endpoint)


@router.delete("/endpoints/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_webhook_endpoint(
    endpoint_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Remove an endpoint, and everything still queued for it."""
    try:
        delete_endpoint(db, current_user, endpoint_id)
    except EndpointNotFoundError as exc:
        raise _not_found() from exc

    record_event(db, EventName.WEBHOOK_ENDPOINT_DELETED, user=current_user)
    record_audit(
        db,
        AuditAction.WEBHOOK_ENDPOINT_DELETED,
        user=current_user,
        context={"endpoint_id": str(endpoint_id)},
        **request_origin(request),
    )


@router.get(
    "/endpoints/{endpoint_id}/secret",
    response_model=WebhookEndpointSecret,
    dependencies=[Depends(webhook_secret_rate_limit)],
)
def reveal_webhook_secret(
    endpoint_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WebhookEndpointSecret:
    """Show an endpoint's signing secret again.

    Its own route rather than a field on the listing, so that the common
    case — a dashboard rendering a table of endpoints — never carries
    secrets through a proxy log or leaves them in a browser's memory. The
    request is audited for the same reason it is rate limited: a burst of
    reveals is what exfiltration looks like, and the owner should be able
    to see one in their own security trail.
    """
    try:
        endpoint = get_endpoint(db, current_user, endpoint_id)
    except EndpointNotFoundError as exc:
        raise _not_found() from exc

    record_audit(
        db,
        AuditAction.WEBHOOK_SECRET_REVEALED,
        user=current_user,
        context={"endpoint_id": str(endpoint_id)},
        **request_origin(request),
    )
    return WebhookEndpointSecret(id=endpoint.id, secret=endpoint.secret)


@router.post(
    "/endpoints/{endpoint_id}/rotate-secret",
    response_model=WebhookEndpointSecret,
    dependencies=[Depends(webhook_secret_rate_limit)],
)
def rotate_webhook_secret(
    endpoint_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WebhookEndpointSecret:
    """Replace the signing secret. Takes effect on the next delivery.

    There is no overlap window: a receiver still checking the old secret
    will reject deliveries until it is updated. That is the honest reading
    of rotating a secret, and it is stated here as well as in the UI because
    the alternative — finding out from a customer's failed deliveries — is a
    bad way to learn it.
    """
    try:
        endpoint = rotate_secret(db, current_user, endpoint_id)
    except EndpointNotFoundError as exc:
        raise _not_found() from exc

    record_audit(
        db,
        AuditAction.WEBHOOK_SECRET_ROTATED,
        user=current_user,
        context={"endpoint_id": str(endpoint_id)},
        **request_origin(request),
    )
    return WebhookEndpointSecret(id=endpoint.id, secret=endpoint.secret)


@router.post(
    "/endpoints/{endpoint_id}/ping",
    response_model=WebhookDeliveryRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(webhook_ping_rate_limit)],
)
def ping_webhook_endpoint(
    endpoint_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WebhookDeliveryRead:
    """Queue a test delivery to one endpoint.

    202 rather than 200: the delivery is queued here and sent by the worker,
    so a response saying "done" would be claiming something this request
    does not know. The returned row is what the caller polls — the delivery
    log shows the outcome once a worker has run.

    Queued even when the endpoint is switched off. Sending a test to an
    endpoint you have just re-enabled is the whole point of a test, and
    refusing it would mean the only way to check a receiver was to wait for
    a real event.
    """
    try:
        endpoint = get_endpoint(db, current_user, endpoint_id)
    except EndpointNotFoundError as exc:
        raise _not_found() from exc

    delivery = queue_delivery(
        db,
        endpoint,
        WebhookEventType.PING,
        {"endpoint_id": str(endpoint.id), "message": "Test delivery"},
    )
    return WebhookDeliveryRead.model_validate(delivery)


@router.get("/deliveries", response_model=list[WebhookDeliveryRead])
def list_webhook_deliveries(
    endpoint_id: uuid.UUID | None = None,
    limit: int = Query(default=DEFAULT_DELIVERY_PAGE_SIZE, ge=1, le=MAX_DELIVERY_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[WebhookDeliveryRead]:
    """Recent deliveries for this account, newest first.

    The payload is deliberately absent from the response shape: it is a copy
    of data the account can already read through the rest of the API, and
    including it would make this listing the widest response in the product
    for no question it answers. What a debugging session needs — the status
    code, the error, how many attempts, when the next one is — is here.
    """
    return [
        WebhookDeliveryRead.model_validate(delivery)
        for delivery in list_deliveries(
            db, current_user, endpoint_id=endpoint_id, limit=limit, offset=offset
        )
    ]

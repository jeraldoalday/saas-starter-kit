"""Queueing outbound webhooks, and the worker half that sends them.

Two halves that never run in the same process, which is the whole reason
this module exists as a unit rather than as part of `endpoints`.

`dispatch_event` runs inside the request that caused the event. It writes a
row per subscribed endpoint and returns. It never opens a socket, and it
never raises: a customer whose receiver is unreachable must not be able to
fail somebody else's API call, and neither must a bug in this code. The
model is `analytics_service.record_event` — best-effort, logged on failure —
with the difference that a lost delivery is worth an operational alert,
because unlike a usage event it is a promise to a third party.

`deliver_due` runs in `app/scripts/deliver_webhooks.py`, on a schedule. It
claims what is due, POSTs it, and decides what each outcome means: retry
with backoff, give up, or switch the endpoint off. It is allowed to be slow,
because nothing is waiting on it.

Putting the POST in the request path instead would have been fewer moving
parts and one specific disaster: a receiver that accepts a connection and
never answers would add its timeout to the latency of the API call that
triggered it, turning one customer's outage into everyone's.
"""

import json
import logging
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutils import as_utc
from app.models.user import User
from app.models.webhook_delivery import (
    MAX_ERROR_LENGTH,
    DeliveryStatus,
    WebhookDelivery,
)
from app.models.webhook_endpoint import WebhookEndpoint, WebhookEventType
from app.services.alerts import Severity, notify
from app.services.webhooks.endpoints import disable_endpoint
from app.services.webhooks.signing import canonical_body, delivery_headers

logger = logging.getLogger("app.webhooks")

#: How long to wait before each retry, indexed by the number of attempts
#: already made. Five entries, so a delivery is attempted six times across
#: roughly nine hours before it is abandoned.
#:
#: The shape matters more than the numbers: the first retry is fast because
#: the most common failure is a receiver restarting, and the last is hours
#: later because the second most common is a receiver that is down for the
#: afternoon. A flat one-minute retry would hammer the second case 500 times
#: and still give up before it recovered.
RETRY_BACKOFF: tuple[timedelta, ...] = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=6),
)

MAX_ATTEMPTS = len(RETRY_BACKOFF) + 1

#: Consecutive failed attempts before an endpoint is switched off. Counted
#: in attempts rather than in abandoned deliveries because attempts are what
#: cost something; at six attempts per delivery this is between three and
#: four dead deliveries in a row, which is a receiver that is gone rather
#: than a receiver having a bad minute.
AUTO_DISABLE_AFTER_FAILURES = 20

#: A response body is not read beyond this. A receiver answering with a
#: stack trace or an HTML error page has said everything useful in its first
#: line, and the column it lands in holds 255 characters.
MAX_RESPONSE_SNIPPET = 512


class DeliveryResult(NamedTuple):
    """What one HTTP attempt did.

    `status_code` is None when the request never got a response at all —
    DNS failure, refused connection, timeout. That distinction is the first
    thing anyone debugging a webhook wants: a 500 means the handler ran and
    threw, no status at all means it was never reached.
    """

    ok: bool
    status_code: int | None
    error: str | None


#: The transport, as a callable, so tests substitute a function instead of
#: patching urllib. Same shape as the alert senders: one function, no
#: session, no retries of its own — retrying is the queue's job and a
#: transport that also retried would multiply the two schedules together.
Transport = Callable[[str, str, dict[str, str], float], DeliveryResult]


@dataclass
class DeliveryRun:
    """What one pass of the worker did, for the CLI to print."""

    attempted: int = 0
    delivered: int = 0
    retrying: int = 0
    abandoned: int = 0
    endpoints_disabled: int = 0

    def __str__(self) -> str:
        return (
            f"attempted={self.attempted} delivered={self.delivered} "
            f"retrying={self.retrying} abandoned={self.abandoned} "
            f"endpoints_disabled={self.endpoints_disabled}"
        )


def _now() -> datetime:
    return datetime.now(UTC)


def post_json(
    url: str, body: str, headers: dict[str, str], timeout_seconds: float
) -> DeliveryResult:
    """POST one signed body. urllib, for the reason the alert sender gives.

    Any 2xx is success. Everything else — including a redirect — is a
    failure, and redirects are deliberately not followed: a receiver that
    answers 302 has pointed the delivery somewhere that was never validated
    as a destination, which would hand back the SSRF protection that
    `urls.validate_webhook_url` exists to provide.
    """
    request = urllib.request.Request(
        url, data=body.encode("utf-8"), headers=headers, method="POST"
    )

    try:
        # A timeout is mandatory: without one a receiver that accepts the
        # connection and never answers holds this worker forever, and the
        # queue behind it stops moving for every other customer.
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = int(response.status)
            if 200 <= status < 300:
                return DeliveryResult(True, status, None)
            return DeliveryResult(False, status, f"HTTP {status}")
    except urllib.error.HTTPError as exc:
        # urllib raises rather than returns for 4xx and 5xx. The body may
        # carry the receiver's own error message, which is the single most
        # useful thing to show the customer, so a little of it is kept.
        snippet = ""
        try:
            snippet = exc.read(MAX_RESPONSE_SNIPPET).decode("utf-8", "replace").strip()
        except (OSError, ValueError):  # pragma: no cover - body already consumed
            snippet = ""
        detail = f"HTTP {exc.code}"
        if snippet:
            detail = f"{detail}: {snippet.splitlines()[0]}"
        return DeliveryResult(False, int(exc.code), detail)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # No response at all. `reason` is the readable half of a URLError;
        # the exception's repr would put the class name in a column the
        # customer reads.
        reason = getattr(exc, "reason", exc)
        return DeliveryResult(False, None, str(reason) or exc.__class__.__name__)


def build_payload(
    event_type: str, data: dict[str, Any], *, delivery_id: uuid.UUID, now: datetime
) -> dict[str, Any]:
    """The envelope one delivery carries.

    An envelope rather than the bare `data`, because a receiver needs three
    things the data itself does not carry: what kind of event this is
    (so one handler can switch), when it happened (so an out-of-order
    delivery can be recognised as stale), and a stable id (so a
    re-delivery can be discarded). The id is the delivery's own, which is
    the same value the `X-Webhook-Delivery` header carries.
    """
    return {
        "id": str(delivery_id),
        "type": event_type,
        "created_at": now.isoformat(),
        "data": data,
    }


def _endpoints_wanting(
    db: Session, user_id: uuid.UUID, event_type: str
) -> list[WebhookEndpoint]:
    stmt = select(WebhookEndpoint).where(
        WebhookEndpoint.user_id == user_id,
        WebhookEndpoint.is_active.is_(True),
        WebhookEndpoint.disabled_at.is_(None),
    )
    endpoints = db.execute(stmt).scalars().all()
    return [endpoint for endpoint in endpoints if endpoint.subscribes_to(event_type)]


def queue_delivery(
    db: Session,
    endpoint: WebhookEndpoint,
    event_type: str | WebhookEventType,
    data: dict[str, Any],
    *,
    now: datetime | None = None,
    commit: bool = True,
) -> WebhookDelivery:
    """Queue one event for one endpoint, due immediately.

    The id is generated here rather than left to the database default,
    because it goes inside the payload — and the payload has to be built
    before the row is written for the two to agree.
    """
    now = now or _now()
    delivery_id = uuid.uuid4()

    delivery = WebhookDelivery(
        id=delivery_id,
        endpoint_id=endpoint.id,
        event_type=str(event_type),
        payload=build_payload(str(event_type), data, delivery_id=delivery_id, now=now),
        status=DeliveryStatus.PENDING.value,
        attempts=0,
        next_attempt_at=now,
        created_at=now,
    )
    db.add(delivery)
    if commit:
        db.commit()
    return delivery


def dispatch_event(
    db: Session,
    event_type: str | WebhookEventType,
    data: dict[str, Any] | None = None,
    *,
    user: User | None = None,
    user_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> list[WebhookDelivery]:
    """Queue one event for every endpoint of the account that wants it.

    Pass `user` when the caller already holds the row and `user_id` when it
    only has the id — the same pair `analytics_service.record_event` takes,
    and for the same caller: the Stripe webhook handler works from a
    subscription and should not load a user purely to name one.

    Returns the deliveries created, which is an empty list in every ordinary
    case: most accounts register no endpoints at all, and this is called
    from the middle of request handlers that must not care.

    Never raises. A failure here is logged and alerted rather than
    propagated, because the alternative is an API call that did its work and
    then returned 500 because a bookkeeping insert failed — the caller
    retries, the work happens twice, and the webhook still was not sent.
    """
    if not get_settings().webhooks_enabled:
        return []

    owner_id = user.id if user is not None else user_id
    if owner_id is None:
        return []

    try:
        endpoints = _endpoints_wanting(db, owner_id, str(event_type))
        if not endpoints:
            return []

        now = now or _now()
        deliveries = [
            queue_delivery(db, endpoint, event_type, data or {}, now=now, commit=False)
            for endpoint in endpoints
        ]
        db.commit()
    except SQLAlchemyError:
        logger.exception(
            "failed to queue webhook deliveries", extra={"webhook_event": str(event_type)}
        )
        db.rollback()
        notify(
            title="Webhook dispatch failed",
            message=(
                f"Could not queue deliveries for {event_type}. Subscribers "
                "were not notified of this event and there is nothing to retry."
            ),
            severity=Severity.WARNING,
            key="webhook-dispatch-failed",
        )
        return []

    logger.info(
        "queued webhook deliveries",
        extra={"webhook_event": str(event_type), "deliveries": len(deliveries)},
    )
    return deliveries


def due_deliveries(
    db: Session, *, limit: int, now: datetime | None = None
) -> list[WebhookDelivery]:
    """Pending deliveries whose next attempt is due, oldest first.

    The clock is normalised before it is bound into the comparison, for the
    reason `api_key_service.purge_revoked_keys` spells out: SQLite stores
    naive UTC and its driver ignores tzinfo, so a caller passing an aware
    datetime in another zone would have its wall-clock reading compared as
    though it were UTC.
    """
    now = as_utc(now) or _now()
    stmt = (
        select(WebhookDelivery)
        .where(
            WebhookDelivery.status == DeliveryStatus.PENDING.value,
            WebhookDelivery.next_attempt_at <= now,
        )
        .order_by(WebhookDelivery.next_attempt_at)
        .limit(max(limit, 1))
    )
    return list(db.execute(stmt).scalars().all())


def _record_success(
    db: Session,
    delivery: WebhookDelivery,
    endpoint: WebhookEndpoint,
    result: DeliveryResult,
    now: datetime,
) -> None:
    delivery.status = DeliveryStatus.DELIVERED.value
    delivery.delivered_at = now
    delivery.next_attempt_at = None
    delivery.last_status_code = result.status_code
    delivery.last_error = None

    endpoint.last_success_at = now
    # Reset rather than decrement: the counter answers "is this endpoint
    # gone?", and one success is a complete answer to that question.
    endpoint.consecutive_failures = 0
    db.commit()


def _record_failure(
    db: Session,
    delivery: WebhookDelivery,
    endpoint: WebhookEndpoint,
    result: DeliveryResult,
    now: datetime,
    run: DeliveryRun,
) -> None:
    delivery.last_status_code = result.status_code
    delivery.last_error = (result.error or "delivery failed")[:MAX_ERROR_LENGTH]

    endpoint.last_failure_at = now
    endpoint.consecutive_failures += 1

    if delivery.attempts >= MAX_ATTEMPTS:
        delivery.status = DeliveryStatus.FAILED.value
        delivery.next_attempt_at = None
        run.abandoned += 1
    else:
        # attempts is 1-based by the time this runs, and the schedule is
        # 0-indexed by attempts *already made*, so the first failure waits
        # RETRY_BACKOFF[0].
        delivery.next_attempt_at = now + RETRY_BACKOFF[delivery.attempts - 1]
        run.retrying += 1

    db.commit()

    if (
        endpoint.consecutive_failures >= AUTO_DISABLE_AFTER_FAILURES
        and endpoint.disabled_at is None
    ):
        disable_endpoint(
            db,
            endpoint,
            reason=(
                f"{endpoint.consecutive_failures} consecutive failed deliveries. "
                f"Last error: {delivery.last_error}"
            ),
            now=now,
        )
        run.endpoints_disabled += 1
        notify(
            title="Webhook endpoint disabled",
            message=(
                f"{endpoint.url} failed {endpoint.consecutive_failures} deliveries "
                "in a row and has been switched off. Its owner can re-enable it "
                "from the dashboard once the receiver is fixed."
            ),
            severity=Severity.INFO,
            key=f"webhook-endpoint-disabled:{endpoint.id}",
        )


def attempt_delivery(
    db: Session,
    delivery: WebhookDelivery,
    *,
    transport: Transport = post_json,
    timeout_seconds: float | None = None,
    now: datetime | None = None,
    run: DeliveryRun | None = None,
) -> DeliveryResult:
    """Send one delivery once and record what happened.

    The attempt counter is incremented before the request rather than after,
    and that ordering is the one that survives a crash: a worker killed
    mid-POST leaves a row that has been tried, and the receiver may well
    have processed it. Counting afterwards would leave the same row looking
    untried, and it would be retried forever by every worker that picked it
    up.
    """
    now = now or _now()
    run = run if run is not None else DeliveryRun()
    settings = get_settings()
    timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else settings.webhook_delivery_timeout_seconds
    )

    endpoint = db.get(WebhookEndpoint, delivery.endpoint_id)
    if endpoint is None:  # pragma: no cover - cascade makes this unreachable
        delivery.status = DeliveryStatus.FAILED.value
        delivery.next_attempt_at = None
        delivery.last_error = "endpoint no longer exists"
        db.commit()
        return DeliveryResult(False, None, delivery.last_error)

    delivery.attempts += 1
    db.commit()
    run.attempted += 1

    body = canonical_body(delivery.payload)
    headers = delivery_headers(
        endpoint.secret,
        body,
        event_type=delivery.event_type,
        delivery_id=str(delivery.id),
        attempt=delivery.attempts,
        timestamp=int(now.timestamp()),
    )

    result = transport(endpoint.url, body, headers, timeout)

    if result.ok:
        _record_success(db, delivery, endpoint, result, now)
        run.delivered += 1
        logger.info(
            "webhook delivered",
            extra={
                "webhook_delivery_id": str(delivery.id),
                "webhook_endpoint_id": str(endpoint.id),
                "attempts": delivery.attempts,
            },
        )
    else:
        _record_failure(db, delivery, endpoint, result, now, run)
        logger.warning(
            "webhook delivery failed",
            extra={
                "webhook_delivery_id": str(delivery.id),
                "webhook_endpoint_id": str(endpoint.id),
                "attempts": delivery.attempts,
                "status_code": result.status_code,
            },
        )

    return result


def deliver_due(
    db: Session,
    *,
    limit: int | None = None,
    transport: Transport = post_json,
    now: datetime | None = None,
) -> DeliveryRun:
    """Send everything currently due, up to `limit`. One pass, no loop.

    A bounded pass rather than "drain the queue" so the worker's runtime is
    predictable: a backlog is worked through over several scheduled runs
    instead of one run that lasts an unknown number of hours and cannot be
    deployed over.
    """
    settings = get_settings()
    batch = limit if limit is not None else settings.webhook_delivery_batch_size
    run = DeliveryRun()

    for delivery in due_deliveries(db, limit=batch, now=now):
        attempt_delivery(db, delivery, transport=transport, now=now, run=run)

    if run.attempted:
        logger.info("webhook delivery pass complete", extra={"result": str(run)})
    return run


def purge_deliveries(
    db: Session, *, older_than_days: int, now: datetime | None = None
) -> int:
    """Delete delivery history past the retention window; returns rows removed.

    Terminal rows only. A pending delivery older than the window is not
    swept up with them: it is still owed to somebody, and deleting it would
    be dropping a promise quietly to save a row. If pending rows are
    accumulating past the retention window, the worker is not running, and
    that is a problem the prune must not hide.
    """
    now = as_utc(now) or _now()
    cutoff = now - timedelta(days=max(older_than_days, 1))
    deleted = (
        db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.status.in_(
                (DeliveryStatus.DELIVERED.value, DeliveryStatus.FAILED.value)
            ),
            WebhookDelivery.created_at < cutoff,
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info("pruned webhook deliveries", extra={"deleted": deleted})
    return int(deleted)


def pending_count(db: Session) -> int:
    """How many deliveries are queued — for the admin overview and alerts."""
    stmt = select(WebhookDelivery).where(
        WebhookDelivery.status == DeliveryStatus.PENDING.value
    )
    return len(list(db.execute(stmt).scalars().all()))


def example_payload(event_type: str | WebhookEventType) -> str:
    """A signed-body example for the docs and the dashboard.

    Built through `canonical_body` rather than hand-written in the docs, so
    the example a customer copies cannot drift from the bytes the signature
    actually covers.
    """
    payload = build_payload(
        str(event_type),
        {"example": True},
        delivery_id=uuid.UUID(int=0),
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return json.dumps(json.loads(canonical_body(payload)), indent=2, sort_keys=True)


__all__ = [
    "AUTO_DISABLE_AFTER_FAILURES",
    "MAX_ATTEMPTS",
    "RETRY_BACKOFF",
    "DeliveryResult",
    "DeliveryRun",
    "Transport",
    "attempt_delivery",
    "build_payload",
    "deliver_due",
    "dispatch_event",
    "due_deliveries",
    "example_payload",
    "pending_count",
    "post_json",
    "purge_deliveries",
    "queue_delivery",
]

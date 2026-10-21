"""Queueing, sending, retrying, giving up, and switching an endpoint off.

The transport is substituted throughout: these tests are about what the
queue decides, not about urllib. The one thing they do assert about the
wire is that what the transport is handed verifies against the endpoint's
secret, because that is the seam where a signing change would break every
customer at once and nothing else would notice.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.security import hash_password
from app.core.timeutils import as_utc
from app.models.user import User
from app.models.webhook_delivery import DeliveryStatus, WebhookDelivery
from app.models.webhook_endpoint import WebhookEndpoint, WebhookEventType
from app.services.alerts import (
    AlertRouter,
    InMemoryAlertSender,
    Severity,
    get_alert_router,
    set_alert_router,
)
from app.services.webhooks.dispatch import (
    AUTO_DISABLE_AFTER_FAILURES,
    MAX_ATTEMPTS,
    RETRY_BACKOFF,
    DeliveryResult,
    attempt_delivery,
    build_payload,
    deliver_due,
    dispatch_event,
    due_deliveries,
    example_payload,
    pending_count,
    purge_deliveries,
    queue_delivery,
)
from app.services.webhooks.signing import SIGNATURE_HEADER, verify_signature

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


@pytest.fixture
def user(db_session) -> User:
    account = User(
        email="hooks@example.com", hashed_password=hash_password("s3cret-password")
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


@pytest.fixture
def endpoint(db_session, user) -> WebhookEndpoint:
    row = WebhookEndpoint(
        user_id=user.id,
        url="https://receiver.example.com/hooks",
        description="Test receiver",
        secret="whsec_test-secret",
        event_types=[WebhookEventType.API_KEY_CREATED.value],
        created_at=NOW,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


class RecordingTransport:
    """A transport that records what it was handed and answers to order."""

    def __init__(self, *results: DeliveryResult) -> None:
        self.results = list(results) or [DeliveryResult(True, 200, None)]
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def __call__(self, url, body, headers, timeout_seconds):
        self.calls.append((url, body, headers))
        # The last result repeats, so a test that wants "always fails" does
        # not have to count the attempts the schedule will make.
        return self.results[min(len(self.calls) - 1, len(self.results) - 1)]


def ok() -> DeliveryResult:
    return DeliveryResult(True, 200, None)


def server_error() -> DeliveryResult:
    return DeliveryResult(False, 500, "HTTP 500")


def unreachable() -> DeliveryResult:
    return DeliveryResult(False, None, "Connection refused")


@pytest.fixture
def alerts():
    """Install an inspectable alert router, and restore the real one after."""
    sender = InMemoryAlertSender()
    installed = AlertRouter(sender, min_severity=Severity.INFO)
    previous = get_alert_router()
    set_alert_router(installed)
    yield sender
    set_alert_router(previous)


# --- queueing -------------------------------------------------------------


def test_an_event_is_queued_for_a_subscribed_endpoint(db_session, user, endpoint):
    deliveries = dispatch_event(
        db_session, WebhookEventType.API_KEY_CREATED, {"prefix": "sk_live_abc"}, user=user
    )

    assert len(deliveries) == 1
    assert deliveries[0].endpoint_id == endpoint.id
    assert deliveries[0].status == DeliveryStatus.PENDING.value
    assert deliveries[0].payload["data"] == {"prefix": "sk_live_abc"}


def test_an_unsubscribed_event_queues_nothing(db_session, user, endpoint):
    assert dispatch_event(db_session, WebhookEventType.API_KEY_REVOKED, user=user) == []


def test_a_ping_reaches_an_endpoint_subscribed_to_nothing(db_session, user, endpoint):
    """The test button has to work before anything is subscribed, which is
    exactly when someone is checking whether their receiver is wired up."""
    endpoint.event_types = []
    db_session.commit()

    assert len(dispatch_event(db_session, WebhookEventType.PING, user=user)) == 1


def test_a_switched_off_endpoint_is_not_queued_for(db_session, user, endpoint):
    endpoint.is_active = False
    db_session.commit()

    assert dispatch_event(db_session, WebhookEventType.API_KEY_CREATED, user=user) == []


def test_an_auto_disabled_endpoint_is_not_queued_for(db_session, user, endpoint):
    endpoint.disabled_at = NOW
    endpoint.disabled_reason = "too many failures"
    db_session.commit()

    assert dispatch_event(db_session, WebhookEventType.API_KEY_CREATED, user=user) == []


def test_another_accounts_endpoint_is_never_queued_for(db_session, user, endpoint):
    stranger = User(
        email="stranger@example.com", hashed_password=hash_password("s3cret-password")
    )
    db_session.add(stranger)
    db_session.commit()

    assert (
        dispatch_event(db_session, WebhookEventType.API_KEY_CREATED, user=stranger) == []
    )


def test_dispatch_by_user_id_matches_dispatch_by_user(db_session, user, endpoint):
    """The Stripe webhook handler has an id and no row; both paths must agree."""
    assert (
        len(dispatch_event(db_session, WebhookEventType.API_KEY_CREATED, user_id=user.id))
        == 1
    )


def test_dispatch_without_an_account_queues_nothing(db_session, endpoint):
    assert dispatch_event(db_session, WebhookEventType.API_KEY_CREATED) == []


def test_dispatch_is_off_when_webhooks_are_disabled(db_session, user, endpoint, monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "webhooks_enabled", False)

    assert dispatch_event(db_session, WebhookEventType.API_KEY_CREATED, user=user) == []


def test_a_queueing_failure_does_not_propagate(db_session, user, endpoint, monkeypatch):
    """A broken insert must not turn into a 500 for the request that
    triggered the event the webhook was meant to report."""

    def _explode():
        raise SQLAlchemyError("disk on fire")

    monkeypatch.setattr(db_session, "commit", _explode)

    assert dispatch_event(db_session, WebhookEventType.API_KEY_CREATED, user=user) == []


def test_a_queueing_failure_rolls_back_and_raises_an_alert(
    db_session, user, endpoint, monkeypatch, alerts
):
    def _explode():
        raise SQLAlchemyError("disk on fire")

    monkeypatch.setattr(db_session, "commit", _explode)

    dispatch_event(db_session, WebhookEventType.API_KEY_CREATED, user=user)

    # No half-written delivery survives the failed commit.
    assert db_session.query(WebhookDelivery).count() == 0
    # And subscribers were not silently left unnotified: someone who can act
    # on it was told.
    assert alerts.titles() == ["Webhook dispatch failed"]


def test_one_event_fans_out_to_every_subscribed_endpoint(db_session, user, endpoint):
    db_session.add(
        WebhookEndpoint(
            user_id=user.id,
            url="https://second.example.com/hooks",
            secret="whsec_second",
            event_types=[WebhookEventType.API_KEY_CREATED.value],
            created_at=NOW,
        )
    )
    db_session.commit()

    assert len(dispatch_event(db_session, WebhookEventType.API_KEY_CREATED, user=user)) == 2


def test_the_payload_carries_its_own_delivery_id(db_session, user, endpoint):
    """What lets a receiver discard a re-delivery."""
    delivery = dispatch_event(
        db_session, WebhookEventType.API_KEY_CREATED, user=user
    )[0]

    assert delivery.payload["id"] == str(delivery.id)
    assert delivery.payload["type"] == WebhookEventType.API_KEY_CREATED.value


# --- one attempt ----------------------------------------------------------


def test_a_successful_attempt_marks_the_delivery_delivered(db_session, user, endpoint):
    delivery = queue_delivery(db_session, endpoint, WebhookEventType.PING, {}, now=NOW)
    transport = RecordingTransport(ok())

    attempt_delivery(db_session, delivery, transport=transport, now=NOW)

    assert delivery.status == DeliveryStatus.DELIVERED.value
    assert as_utc(delivery.delivered_at) == NOW
    assert delivery.next_attempt_at is None
    assert delivery.attempts == 1
    assert delivery.last_status_code == 200


def test_the_body_the_transport_receives_verifies_against_the_secret(
    db_session, user, endpoint
):
    """The seam where a signing change would break every customer at once."""
    delivery = queue_delivery(db_session, endpoint, WebhookEventType.PING, {}, now=NOW)
    transport = RecordingTransport(ok())

    attempt_delivery(db_session, delivery, transport=transport, now=NOW)

    url, body, headers = transport.calls[0]
    assert url == endpoint.url
    assert verify_signature(endpoint.secret, body, headers[SIGNATURE_HEADER], now=NOW)


def test_a_success_resets_the_endpoints_failure_counter(db_session, user, endpoint):
    endpoint.consecutive_failures = 7
    db_session.commit()
    delivery = queue_delivery(db_session, endpoint, WebhookEventType.PING, {}, now=NOW)

    attempt_delivery(db_session, delivery, transport=RecordingTransport(ok()), now=NOW)

    assert endpoint.consecutive_failures == 0
    assert as_utc(endpoint.last_success_at) == NOW


def test_a_failed_attempt_schedules_the_first_retry(db_session, user, endpoint):
    delivery = queue_delivery(db_session, endpoint, WebhookEventType.PING, {}, now=NOW)

    attempt_delivery(
        db_session, delivery, transport=RecordingTransport(server_error()), now=NOW
    )

    assert delivery.status == DeliveryStatus.PENDING.value
    assert delivery.attempts == 1
    assert as_utc(delivery.next_attempt_at) == NOW + RETRY_BACKOFF[0]
    assert delivery.last_status_code == 500
    assert endpoint.consecutive_failures == 1


def test_a_transport_error_records_no_status_code(db_session, user, endpoint):
    """"Never reached" and "the handler threw" are the first distinction
    anyone debugging a webhook wants."""
    delivery = queue_delivery(db_session, endpoint, WebhookEventType.PING, {}, now=NOW)

    attempt_delivery(
        db_session, delivery, transport=RecordingTransport(unreachable()), now=NOW
    )

    assert delivery.last_status_code is None
    assert delivery.last_error == "Connection refused"


def test_the_backoff_lengthens_with_each_attempt(db_session, user, endpoint):
    delivery = queue_delivery(db_session, endpoint, WebhookEventType.PING, {}, now=NOW)
    transport = RecordingTransport(server_error())

    for index in range(len(RETRY_BACKOFF)):
        attempt_delivery(db_session, delivery, transport=transport, now=NOW)
        assert as_utc(delivery.next_attempt_at) == NOW + RETRY_BACKOFF[index]


def test_a_delivery_is_abandoned_after_the_last_attempt(db_session, user, endpoint):
    delivery = queue_delivery(db_session, endpoint, WebhookEventType.PING, {}, now=NOW)
    transport = RecordingTransport(server_error())

    for _ in range(MAX_ATTEMPTS):
        attempt_delivery(db_session, delivery, transport=transport, now=NOW)

    assert delivery.attempts == MAX_ATTEMPTS
    assert delivery.status == DeliveryStatus.FAILED.value
    assert delivery.next_attempt_at is None
    assert delivery.is_terminal is True


def test_a_recovery_mid_schedule_still_delivers(db_session, user, endpoint):
    """The case the backoff exists for: a receiver that was restarting."""
    delivery = queue_delivery(db_session, endpoint, WebhookEventType.PING, {}, now=NOW)
    transport = RecordingTransport(server_error(), server_error(), ok())

    for _ in range(3):
        attempt_delivery(db_session, delivery, transport=transport, now=NOW)

    assert delivery.status == DeliveryStatus.DELIVERED.value
    assert delivery.attempts == 3


# --- auto-disable ---------------------------------------------------------


def test_an_endpoint_is_switched_off_after_enough_consecutive_failures(
    db_session, user, endpoint
):
    endpoint.consecutive_failures = AUTO_DISABLE_AFTER_FAILURES - 1
    db_session.commit()
    delivery = queue_delivery(db_session, endpoint, WebhookEventType.PING, {}, now=NOW)

    attempt_delivery(
        db_session, delivery, transport=RecordingTransport(unreachable()), now=NOW
    )

    assert as_utc(endpoint.disabled_at) == NOW
    assert "consecutive failed deliveries" in endpoint.disabled_reason
    # The owner's own switch is untouched, so turning it back on stays their
    # decision rather than depending on which switch stopped it.
    assert endpoint.is_active is True
    assert endpoint.is_deliverable is False


def test_an_endpoint_short_of_the_threshold_stays_on(db_session, user, endpoint):
    endpoint.consecutive_failures = AUTO_DISABLE_AFTER_FAILURES - 2
    db_session.commit()
    delivery = queue_delivery(db_session, endpoint, WebhookEventType.PING, {}, now=NOW)

    attempt_delivery(
        db_session, delivery, transport=RecordingTransport(server_error()), now=NOW
    )

    assert endpoint.disabled_at is None
    assert endpoint.is_deliverable is True


# --- the worker pass ------------------------------------------------------


def test_only_due_deliveries_are_claimed(db_session, user, endpoint):
    due = queue_delivery(db_session, endpoint, WebhookEventType.PING, {}, now=NOW)
    later = queue_delivery(db_session, endpoint, WebhookEventType.PING, {}, now=NOW)
    later.next_attempt_at = NOW + timedelta(hours=1)
    db_session.commit()

    claimed = due_deliveries(db_session, limit=10, now=NOW)

    assert [row.id for row in claimed] == [due.id]


def test_a_terminal_delivery_is_never_claimed_again(db_session, user, endpoint):
    delivery = queue_delivery(db_session, endpoint, WebhookEventType.PING, {}, now=NOW)
    attempt_delivery(db_session, delivery, transport=RecordingTransport(ok()), now=NOW)

    assert due_deliveries(db_session, limit=10, now=NOW) == []


def test_a_pass_reports_what_it_did(db_session, user, endpoint):
    for _ in range(3):
        queue_delivery(db_session, endpoint, WebhookEventType.PING, {}, now=NOW)

    run = deliver_due(
        db_session,
        limit=10,
        transport=RecordingTransport(ok(), server_error(), ok()),
        now=NOW,
    )

    assert run.attempted == 3
    assert run.delivered == 2
    assert run.retrying == 1
    assert "delivered=2" in str(run)


def test_a_pass_is_bounded_by_its_limit(db_session, user, endpoint):
    for _ in range(5):
        queue_delivery(db_session, endpoint, WebhookEventType.PING, {}, now=NOW)

    run = deliver_due(db_session, limit=2, transport=RecordingTransport(ok()), now=NOW)

    assert run.attempted == 2
    assert pending_count(db_session) == 3


# --- retention ------------------------------------------------------------


def test_the_prune_removes_finished_rows_past_the_window(db_session, user, endpoint):
    old = queue_delivery(
        db_session, endpoint, WebhookEventType.PING, {}, now=NOW - timedelta(days=60)
    )
    old.status = DeliveryStatus.DELIVERED.value
    db_session.commit()
    old_id = old.id

    assert purge_deliveries(db_session, older_than_days=30, now=NOW) == 1
    # Expunged first: the bulk delete does not synchronise the session, so
    # `get` would answer from the identity map rather than from the table.
    db_session.expunge_all()
    assert db_session.get(WebhookDelivery, old_id) is None


def test_the_prune_leaves_a_pending_row_however_old_it_is(db_session, user, endpoint):
    """It is still owed to somebody, and a backlog past the window means the
    worker is not running — which the prune must surface, not tidy away."""
    stale_id = queue_delivery(
        db_session, endpoint, WebhookEventType.PING, {}, now=NOW - timedelta(days=365)
    ).id

    assert purge_deliveries(db_session, older_than_days=30, now=NOW) == 0
    db_session.expunge_all()
    assert db_session.get(WebhookDelivery, stale_id) is not None


def test_the_prune_leaves_recent_finished_rows(db_session, user, endpoint):
    recent = queue_delivery(db_session, endpoint, WebhookEventType.PING, {}, now=NOW)
    recent.status = DeliveryStatus.FAILED.value
    db_session.commit()

    assert purge_deliveries(db_session, older_than_days=30, now=NOW) == 0


def test_deleting_an_endpoint_takes_its_queue_with_it(db_session, user, endpoint):
    queue_delivery(db_session, endpoint, WebhookEventType.PING, {}, now=NOW)

    db_session.delete(endpoint)
    db_session.commit()

    assert pending_count(db_session) == 0


# --- payload shape --------------------------------------------------------


def test_the_envelope_carries_type_time_and_id():
    payload = build_payload(
        "ping", {"a": 1}, delivery_id=__import__("uuid").UUID(int=7), now=NOW
    )

    assert set(payload) == {"id", "type", "created_at", "data"}
    assert payload["created_at"] == NOW.isoformat()


def test_the_documented_example_is_produced_by_the_real_serialiser():
    """So the example a customer copies cannot drift from the signed bytes."""
    rendered = example_payload(WebhookEventType.PING)

    assert '"type": "ping"' in rendered
    assert '"example": true' in rendered

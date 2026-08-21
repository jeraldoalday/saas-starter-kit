import uuid
from datetime import UTC, datetime

import pytest

from app.models.subscription import Subscription, SubscriptionStatus
from app.models.user import User
from app.models.webhook_event import ProcessedWebhookEvent
from app.services import billing_service
from app.services.billing_service import InvalidWebhookSignatureError


@pytest.fixture
def subscribed_user(db_session):
    user = User(email="wh@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()

    subscription = Subscription(
        user_id=user.id,
        stripe_customer_id="cus_1",
        stripe_subscription_id="sub_1",
        status=SubscriptionStatus.ACTIVE.value,
    )
    db_session.add(subscription)
    db_session.commit()
    return user


def _stub_event(monkeypatch, event: dict) -> None:
    monkeypatch.setattr(
        billing_service.stripe.Webhook, "construct_event", lambda *a, **kw: event
    )


def _with_secret(monkeypatch, secret: str = "whsec_test") -> None:
    monkeypatch.setattr(billing_service.settings, "stripe_webhook_secret", secret)


def test_missing_webhook_secret_fails_closed(db_session, monkeypatch):
    _with_secret(monkeypatch, "")

    with pytest.raises(InvalidWebhookSignatureError):
        billing_service.handle_webhook_event(db_session, b"{}", "sig")


def test_subscription_updated_syncs_status_and_period_end(
    db_session, subscribed_user, monkeypatch
):
    _with_secret(monkeypatch)
    period_end = int(datetime(2026, 12, 1, tzinfo=UTC).timestamp())
    _stub_event(
        monkeypatch,
        {
            "id": "evt_1",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_1",
                    "status": "past_due",
                    "current_period_end": period_end,
                }
            },
        },
    )

    billing_service.handle_webhook_event(db_session, b"{}", "sig")

    subscription = db_session.query(Subscription).one()
    db_session.refresh(subscription)
    assert subscription.status == "past_due"
    assert subscription.current_period_end is not None


def test_subscription_deleted_forces_canceled(db_session, subscribed_user, monkeypatch):
    _with_secret(monkeypatch)
    # Stripe can report status "active" on the deletion event for the final
    # paid period; the terminal state must still win.
    _stub_event(
        monkeypatch,
        {
            "id": "evt_2",
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub_1", "status": "active"}},
        },
    )

    billing_service.handle_webhook_event(db_session, b"{}", "sig")

    subscription = db_session.query(Subscription).one()
    db_session.refresh(subscription)
    assert subscription.status == SubscriptionStatus.CANCELED.value


def test_payment_failure_marks_past_due(db_session, subscribed_user, monkeypatch):
    _with_secret(monkeypatch)
    _stub_event(
        monkeypatch,
        {
            "id": "evt_3",
            "type": "invoice.payment_failed",
            "data": {"object": {"subscription": "sub_1"}},
        },
    )

    billing_service.handle_webhook_event(db_session, b"{}", "sig")

    subscription = db_session.query(Subscription).one()
    db_session.refresh(subscription)
    assert subscription.status == SubscriptionStatus.PAST_DUE.value


def test_replayed_event_is_ignored(db_session, subscribed_user, monkeypatch):
    _with_secret(monkeypatch)
    event = {
        "id": "evt_replay",
        "type": "customer.subscription.deleted",
        "data": {"object": {"id": "sub_1", "status": "active"}},
    }
    _stub_event(monkeypatch, event)

    billing_service.handle_webhook_event(db_session, b"{}", "sig")

    # Reinstate the subscription, then redeliver the same event id.
    subscription = db_session.query(Subscription).one()
    subscription.status = SubscriptionStatus.ACTIVE.value
    db_session.commit()

    billing_service.handle_webhook_event(db_session, b"{}", "sig")

    db_session.refresh(subscription)
    assert subscription.status == SubscriptionStatus.ACTIVE.value


def test_processed_event_is_recorded(db_session, subscribed_user, monkeypatch):
    _with_secret(monkeypatch)
    _stub_event(
        monkeypatch,
        {
            "id": "evt_recorded",
            "type": "customer.subscription.updated",
            "data": {"object": {"id": "sub_1", "status": "active"}},
        },
    )

    billing_service.handle_webhook_event(db_session, b"{}", "sig")

    assert db_session.get(ProcessedWebhookEvent, "evt_recorded") is not None


def test_checkout_completed_links_stripe_ids(db_session, monkeypatch):
    user = User(email="checkout@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.add(Subscription(user_id=user.id))
    db_session.commit()

    _with_secret(monkeypatch)
    _stub_event(
        monkeypatch,
        {
            "id": "evt_checkout",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "client_reference_id": str(user.id),
                    "customer": "cus_new",
                    "subscription": "sub_new",
                }
            },
        },
    )

    billing_service.handle_webhook_event(db_session, b"{}", "sig")

    subscription = db_session.query(Subscription).one()
    db_session.refresh(subscription)
    assert subscription.stripe_customer_id == "cus_new"
    assert subscription.stripe_subscription_id == "sub_new"
    assert subscription.status == SubscriptionStatus.ACTIVE.value


def test_unparseable_client_reference_id_is_survived(db_session, monkeypatch):
    _with_secret(monkeypatch)
    _stub_event(
        monkeypatch,
        {
            "id": "evt_bad_ref",
            "type": "checkout.session.completed",
            "data": {"object": {"client_reference_id": "not-a-uuid"}},
        },
    )

    # Must not raise: a malformed id is Stripe's problem, not a 500 for us.
    billing_service.handle_webhook_event(db_session, b"{}", "sig")


def test_unknown_event_type_is_ignored(db_session, monkeypatch):
    _with_secret(monkeypatch)
    _stub_event(
        monkeypatch,
        {"id": "evt_unknown", "type": "customer.created", "data": {"object": {}}},
    )

    billing_service.handle_webhook_event(db_session, b"{}", "sig")

    assert db_session.get(ProcessedWebhookEvent, "evt_unknown") is not None


def test_uuid_user_id_is_matched_as_uuid_not_string(db_session, monkeypatch):
    """client_reference_id arrives as a string but user_id is a UUID column."""
    user = User(email="uuidmatch@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.add(Subscription(user_id=user.id))
    db_session.commit()

    assert isinstance(user.id, uuid.UUID)

    _with_secret(monkeypatch)
    _stub_event(
        monkeypatch,
        {
            "id": "evt_uuid",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "client_reference_id": str(user.id),
                    "customer": "cus_uuid",
                    "subscription": "sub_uuid",
                }
            },
        },
    )

    billing_service.handle_webhook_event(db_session, b"{}", "sig")

    subscription = db_session.query(Subscription).one()
    db_session.refresh(subscription)
    assert subscription.stripe_customer_id == "cus_uuid"


# --- Stripe API version drift ---------------------------------------------
#
# Stripe's 2025-03-31 ("basil") version reshaped two fields this handler
# reads. Which shape arrives depends on the API version configured on the
# Stripe account, not on anything in this repo, so both are covered.


def _payment_failed_event(invoice: dict) -> dict:
    return {
        "id": "evt_invoice",
        "type": "invoice.payment_failed",
        "data": {"object": invoice},
    }


def test_payment_failed_reads_the_legacy_invoice_shape(
    db_session, subscribed_user, monkeypatch
):
    _with_secret(monkeypatch)
    _stub_event(monkeypatch, _payment_failed_event({"subscription": "sub_1"}))

    billing_service.handle_webhook_event(db_session, b"{}", "sig")

    assert db_session.query(Subscription).one().status == "past_due"


def test_payment_failed_reads_the_current_invoice_shape(
    db_session, subscribed_user, monkeypatch
):
    _with_secret(monkeypatch)
    _stub_event(
        monkeypatch,
        _payment_failed_event(
            {"parent": {"subscription_details": {"subscription": "sub_1"}}}
        ),
    )

    billing_service.handle_webhook_event(db_session, b"{}", "sig")

    assert db_session.query(Subscription).one().status == "past_due"


def test_payment_failed_reads_an_expanded_subscription_object(
    db_session, subscribed_user, monkeypatch
):
    _with_secret(monkeypatch)
    _stub_event(monkeypatch, _payment_failed_event({"subscription": {"id": "sub_1"}}))

    billing_service.handle_webhook_event(db_session, b"{}", "sig")

    assert db_session.query(Subscription).one().status == "past_due"


def test_payment_failed_without_a_subscription_is_ignored(
    db_session, subscribed_user, monkeypatch
):
    """One-off invoices carry no subscription; they must not churn anyone."""
    _with_secret(monkeypatch)
    _stub_event(monkeypatch, _payment_failed_event({"id": "in_oneoff"}))

    billing_service.handle_webhook_event(db_session, b"{}", "sig")

    assert db_session.query(Subscription).one().status == "active"


def test_period_end_is_read_from_subscription_items(
    db_session, subscribed_user, monkeypatch
):
    _with_secret(monkeypatch)
    period_end = int(datetime(2027, 3, 1, tzinfo=UTC).timestamp())
    _stub_event(
        monkeypatch,
        {
            "id": "evt_items",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_1",
                    "status": "active",
                    "items": {"data": [{"current_period_end": period_end}]},
                }
            },
        },
    )

    billing_service.handle_webhook_event(db_session, b"{}", "sig")

    subscription = db_session.query(Subscription).one()
    db_session.refresh(subscription)
    assert subscription.current_period_end is not None
    assert subscription.current_period_end.year == 2027

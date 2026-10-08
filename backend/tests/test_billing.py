import uuid

import pytest

from app.models.subscription import Subscription, SubscriptionStatus
from app.models.user import User
from app.services import billing_service
from app.services.billing_service import AlreadySubscribedError, NoStripeCustomerError


def _auth_headers(client, email="billing@example.com", password="s3cret-password"):
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Billing User"},
    )
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_checkout_requires_authentication(client):
    response = client.post("/api/v1/billing/checkout")
    assert response.status_code == 401


def test_portal_requires_authentication(client):
    response = client.post("/api/v1/billing/portal")
    assert response.status_code == 401


def test_subscription_requires_authentication(client):
    response = client.get("/api/v1/billing/subscription")
    assert response.status_code == 401


def test_webhook_rejects_invalid_signature(client):
    response = client.post(
        "/api/v1/billing/webhook",
        content=b'{"type": "checkout.session.completed"}',
        headers={"stripe-signature": "invalid"},
    )
    assert response.status_code == 400


def test_webhook_accepts_a_valid_signature(client, db_session, monkeypatch):
    """The endpoint itself, not just handle_webhook_event: reading the body
    and the signature header, calling the service, and answering 204.
    """
    monkeypatch.setattr(billing_service.settings, "stripe_webhook_secret", "whsec_test")
    monkeypatch.setattr(
        billing_service.stripe.Webhook,
        "construct_event",
        lambda *a, **kw: {"id": "evt_1", "type": "some.unhandled.type", "data": {"object": {}}},
    )

    response = client.post(
        "/api/v1/billing/webhook",
        content=b'{"type": "some.unhandled.type"}',
        headers={"stripe-signature": "valid"},
    )

    assert response.status_code == 204
    assert response.content == b""


def test_subscription_returns_the_current_subscription(client, db_session):
    headers = _auth_headers(client, email="subscribed@example.com")
    me = client.get("/api/v1/users/me", headers=headers).json()
    db_session.add(
        Subscription(
            user_id=uuid.UUID(me["id"]),
            stripe_customer_id="cus_sub_test",
            status=SubscriptionStatus.ACTIVE.value,
        )
    )
    db_session.commit()

    response = client.get("/api/v1/billing/subscription", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == SubscriptionStatus.ACTIVE.value


def test_subscription_is_null_without_one(client):
    headers = _auth_headers(client, email="nosub@example.com")

    response = client.get("/api/v1/billing/subscription", headers=headers)

    assert response.status_code == 200
    assert response.json() is None


def test_checkout_returns_stripe_url(client, monkeypatch):
    class _FakeSession:
        url = "https://checkout.stripe.test/session_123"

    captured = {}

    def _create(**kwargs):
        captured.update(kwargs)
        return _FakeSession()

    monkeypatch.setattr(billing_service.stripe.checkout.Session, "create", _create)

    headers = _auth_headers(client)
    response = client.post("/api/v1/billing/checkout", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"checkout_url": "https://checkout.stripe.test/session_123"}
    assert captured["mode"] == "subscription"
    # Redirect targets come from settings, not hardcoded localhost.
    assert captured["success_url"].endswith("/dashboard?checkout=success")
    assert captured["cancel_url"].endswith("/dashboard?checkout=canceled")


@pytest.mark.parametrize("status", [SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING])
def test_checkout_conflicts_when_already_subscribed(client, db_session, status):
    """Checkout mode "subscription" would open a second one, not refuse.

    Nothing about a resubmitted request proves it is a mistake, so this has
    to be caught before Stripe is ever called.
    """
    headers = _auth_headers(client, email=f"already-{status.value}@example.com")
    me = client.get("/api/v1/users/me", headers=headers).json()
    db_session.add(
        Subscription(user_id=uuid.UUID(me["id"]), status=status.value)
    )
    db_session.commit()

    response = client.post("/api/v1/billing/checkout", headers=headers)

    assert response.status_code == 409
    assert "portal" in response.json()["detail"].lower()


def test_checkout_allowed_once_subscription_is_canceled(client, db_session, monkeypatch):
    class _FakeSession:
        url = "https://checkout.stripe.test/session_456"

    monkeypatch.setattr(
        billing_service.stripe.checkout.Session, "create", lambda **kwargs: _FakeSession()
    )

    headers = _auth_headers(client, email="lapsed@example.com")
    me = client.get("/api/v1/users/me", headers=headers).json()
    db_session.add(
        Subscription(user_id=uuid.UUID(me["id"]), status=SubscriptionStatus.CANCELED.value)
    )
    db_session.commit()

    response = client.post("/api/v1/billing/checkout", headers=headers)

    assert response.status_code == 200


def test_create_checkout_session_raises_for_active_subscription(db_session):
    user = User(email="service-level@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.add(
        Subscription(user_id=user.id, status=SubscriptionStatus.ACTIVE.value)
    )
    db_session.commit()
    db_session.refresh(user)

    with pytest.raises(AlreadySubscribedError):
        billing_service.create_checkout_session(db_session, user)


def test_portal_conflicts_when_no_stripe_customer(client):
    headers = _auth_headers(client)

    response = client.post("/api/v1/billing/portal", headers=headers)

    assert response.status_code == 409
    assert "checkout" in response.json()["detail"].lower()


def test_portal_returns_stripe_url_for_existing_customer(client, db_session, monkeypatch):
    class _FakeSession:
        url = "https://billing.stripe.test/portal_123"

    captured = {}

    def _create(**kwargs):
        captured.update(kwargs)
        return _FakeSession()

    monkeypatch.setattr(billing_service.stripe.billing_portal.Session, "create", _create)

    headers = _auth_headers(client)
    me = client.get("/api/v1/users/me", headers=headers).json()

    # A Stripe customer only exists after a completed checkout; simulate that state.
    db_session.add(
        Subscription(user_id=uuid.UUID(me["id"]), stripe_customer_id="cus_test_123")
    )
    db_session.commit()

    response = client.post("/api/v1/billing/portal", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"portal_url": "https://billing.stripe.test/portal_123"}
    assert captured["customer"] == "cus_test_123"
    assert captured["return_url"].endswith("/dashboard")


def test_create_portal_session_raises_without_customer(db_session):
    user = User(email="nocustomer@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()

    with pytest.raises(NoStripeCustomerError):
        billing_service.create_portal_session(db_session, user)

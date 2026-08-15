import uuid

import pytest

from app.models.subscription import Subscription
from app.models.user import User
from app.services import billing_service
from app.services.billing_service import NoStripeCustomerError


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

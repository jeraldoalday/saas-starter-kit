def test_checkout_requires_authentication(client):
    response = client.post("/api/v1/billing/checkout")
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

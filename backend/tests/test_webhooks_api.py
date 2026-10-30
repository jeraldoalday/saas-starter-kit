"""HTTP surface of outbound webhook management.

Registration is exercised with the insecure-URL escape hatch on, because
the alternative is a test suite that resolves hostnames — the SSRF rules
themselves are covered directly in tests/test_webhook_urls.py, and one test
below keeps the hatch's default (off) honest.
"""

import pytest

from app.core.entitlements import FREE_PLAN, PRO_PLAN
from app.core.rate_limit import rate_limiter
from app.models.audit_log import AuditAction, AuditLog
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.usage_event import EventName, UsageEvent
from app.models.user import User
from app.models.webhook_delivery import DeliveryStatus
from app.models.webhook_endpoint import WebhookEventType
from app.services.webhooks.catalog import EVENT_TYPES, missing_descriptions
from app.services.webhooks.signing import SECRET_PREFIX

EMAIL = "hooks-api@example.com"
PASSWORD = "s3cret-password"
URL = "https://receiver.example.com/hooks"


@pytest.fixture(autouse=True)
def _allow_local_urls(monkeypatch):
    """Skip the destination check for these tests, not the code path.

    `create_endpoint` still runs `validate_webhook_url`; the hatch only
    relaxes what counts as an acceptable destination, which keeps DNS out of
    the test suite without stubbing the validator away.
    """
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "webhook_allow_insecure_urls", True)


def _session(client, email=EMAIL, password=PASSWORD):
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Hook Owner"},
    )
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _create(client, headers, url=URL, **body):
    return client.post(
        "/api/v1/webhooks/endpoints", json={"url": url, **body}, headers=headers
    )


def _subscribe(db_session, email=EMAIL):
    """Put the account on the paid plan: the free tier allows one endpoint."""
    user = db_session.query(User).filter(User.email == email).one()
    db_session.add(Subscription(user_id=user.id, status=SubscriptionStatus.ACTIVE.value))
    db_session.commit()
    return user


# --- the event catalog ----------------------------------------------------


def test_the_event_catalog_is_public(client):
    """A developer evaluating the product reads it before they have an account."""
    response = client.get("/api/v1/webhooks/event-types")

    assert response.status_code == 200
    assert {entry["event_type"] for entry in response.json()} == {
        info.event_type for info in EVENT_TYPES
    }


def test_every_subscribable_event_is_described():
    """A checkbox with no label is what the alternative looks like."""
    assert missing_descriptions() == ()


# --- registration ---------------------------------------------------------


def test_registering_requires_a_session(client):
    assert client.post("/api/v1/webhooks/endpoints", json={"url": URL}).status_code == 401


def test_registration_returns_the_signing_secret(client):
    headers = _session(client)

    response = _create(client, headers, description="Primary receiver")

    assert response.status_code == 201
    body = response.json()
    assert body["secret"].startswith(SECRET_PREFIX)
    assert body["url"] == URL
    assert body["is_deliverable"] is True
    assert body["consecutive_failures"] == 0


def test_an_endpoint_can_subscribe_to_events(client):
    headers = _session(client)

    body = _create(
        client,
        headers,
        event_types=[
            WebhookEventType.API_KEY_REVOKED.value,
            WebhookEventType.API_KEY_CREATED.value,
        ],
    ).json()

    # Sorted, so two endpoints wanting the same events look the same however
    # they were typed.
    assert body["event_types"] == sorted(
        [WebhookEventType.API_KEY_CREATED.value, WebhookEventType.API_KEY_REVOKED.value]
    )


def test_an_unknown_event_type_is_refused_by_name(client):
    headers = _session(client)

    response = _create(client, headers, event_types=["api_key.create"])

    assert response.status_code == 422
    assert "api_key.create" in str(response.json())


def test_subscribing_to_ping_is_refused(client):
    """It reaches every endpoint anyway; allowing it would imply some do not."""
    headers = _session(client)

    assert _create(client, headers, event_types=["ping"]).status_code == 422


def test_an_unsafe_destination_is_refused(client, monkeypatch):
    """The default posture, asserted with the escape hatch back off."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "webhook_allow_insecure_urls", False)
    headers = _session(client)

    response = _create(client, headers, url="http://169.254.169.254/latest/meta-data")

    assert response.status_code == 422
    assert "https" in response.json()["detail"]


def test_registration_records_an_event_and_an_audit_row(client, db_session):
    headers = _session(client)

    _create(client, headers)

    assert (
        db_session.query(UsageEvent)
        .filter(UsageEvent.name == EventName.WEBHOOK_ENDPOINT_CREATED.value)
        .count()
        == 1
    )
    entry = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == AuditAction.WEBHOOK_ENDPOINT_CREATED.value)
        .one()
    )
    # The URL, never the secret: enough to say which endpoint appeared.
    assert entry.context["url"] == URL
    assert "secret" not in entry.context


# --- plan ceilings --------------------------------------------------------


def test_the_free_tier_allows_one_endpoint(client):
    headers = _session(client)
    assert _create(client, headers).status_code == 201

    response = _create(client, headers, url="https://second.example.com/hooks")

    assert response.status_code == 409
    assert str(FREE_PLAN.max_webhook_endpoints) in response.json()["detail"]
    assert FREE_PLAN.display_name in response.json()["detail"]


def test_a_paid_account_gets_the_paid_ceiling(client, db_session):
    headers = _session(client)
    _subscribe(db_session)

    for index in range(PRO_PLAN.max_webhook_endpoints):
        assert (
            _create(client, headers, url=f"https://r{index}.example.com/hooks").status_code
            == 201
        )

    assert _create(client, headers, url="https://over.example.com/hooks").status_code == 409


def test_a_switched_off_endpoint_does_not_hold_a_slot(client):
    """Charging a slot for a receiver the owner has parked is the wrong way round."""
    headers = _session(client)
    created = _create(client, headers).json()

    client.patch(
        f"/api/v1/webhooks/endpoints/{created['id']}",
        json={"is_active": False},
        headers=headers,
    )

    assert _create(client, headers, url="https://second.example.com/hooks").status_code == 201


def test_the_plan_endpoint_reports_the_webhook_quota(client):
    headers = _session(client)
    _create(client, headers)

    quotas = {q["key"]: q for q in client.get("/api/v1/plan", headers=headers).json()["quotas"]}

    assert quotas["webhook_endpoints"]["used"] == 1
    assert quotas["webhook_endpoints"]["limit"] == FREE_PLAN.max_webhook_endpoints
    assert quotas["webhook_endpoints"]["exhausted"] is True


# --- listing and isolation ------------------------------------------------


def test_the_listing_never_carries_secrets(client):
    headers = _session(client)
    _create(client, headers)

    body = client.get("/api/v1/webhooks/endpoints", headers=headers).json()

    assert len(body) == 1
    assert "secret" not in body[0]


def test_one_account_cannot_see_anothers_endpoint(client):
    owner = _session(client)
    created = _create(client, owner).json()
    stranger = _session(client, email="stranger@example.com")

    assert client.get("/api/v1/webhooks/endpoints", headers=stranger).json() == []
    assert (
        client.get(
            f"/api/v1/webhooks/endpoints/{created['id']}", headers=stranger
        ).status_code
        == 404
    )


def test_one_account_cannot_read_anothers_secret(client):
    owner = _session(client)
    created = _create(client, owner).json()
    stranger = _session(client, email="stranger@example.com")

    response = client.get(
        f"/api/v1/webhooks/endpoints/{created['id']}/secret", headers=stranger
    )

    assert response.status_code == 404


def test_a_missing_endpoint_is_a_404(client):
    headers = _session(client)

    response = client.get(
        "/api/v1/webhooks/endpoints/11111111-1111-1111-1111-111111111111",
        headers=headers,
    )

    assert response.status_code == 404


def test_patching_a_missing_endpoint_is_a_404(client):
    headers = _session(client)

    response = client.patch(
        "/api/v1/webhooks/endpoints/11111111-1111-1111-1111-111111111111",
        json={"description": "new"},
        headers=headers,
    )

    assert response.status_code == 404


# --- editing --------------------------------------------------------------


def test_the_subscription_list_can_be_changed(client):
    headers = _session(client)
    created = _create(client, headers).json()

    body = client.patch(
        f"/api/v1/webhooks/endpoints/{created['id']}",
        json={"event_types": [WebhookEventType.SUBSCRIPTION_CANCELED.value]},
        headers=headers,
    ).json()

    assert body["event_types"] == [WebhookEventType.SUBSCRIPTION_CANCELED.value]


def test_an_empty_patch_returns_the_endpoint_unchanged(client):
    """A form re-submitted with nothing edited is not an error."""
    headers = _session(client)
    created = _create(client, headers, description="Primary").json()

    body = client.patch(
        f"/api/v1/webhooks/endpoints/{created['id']}", json={}, headers=headers
    ).json()

    assert body["description"] == "Primary"


def test_editing_an_off_endpoints_description_does_not_claim_it_was_deactivated(
    client, db_session
):
    """is_active only belongs in the audit row when the patch touched it.

    Otherwise an edit to the description or subscription list of an endpoint
    that already happened to be off would read, on the security page, as the
    moment it was switched off.
    """
    headers = _session(client)
    created = _create(client, headers).json()
    client.patch(
        f"/api/v1/webhooks/endpoints/{created['id']}",
        json={"is_active": False},
        headers=headers,
    )

    client.patch(
        f"/api/v1/webhooks/endpoints/{created['id']}",
        json={"description": "Renamed"},
        headers=headers,
    )

    entries = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == AuditAction.WEBHOOK_ENDPOINT_UPDATED.value)
        .order_by(AuditLog.created_at)
        .all()
    )
    assert "is_active" in entries[0].context  # the actual on/off flip
    assert "is_active" not in entries[1].context  # an unrelated rename


def test_toggling_an_endpoint_off_does_record_is_active(client, db_session):
    headers = _session(client)
    created = _create(client, headers).json()

    client.patch(
        f"/api/v1/webhooks/endpoints/{created['id']}",
        json={"is_active": False},
        headers=headers,
    )

    entry = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == AuditAction.WEBHOOK_ENDPOINT_UPDATED.value)
        .one()
    )
    assert entry.context["is_active"] is False


def test_the_url_cannot_be_edited(client):
    """Moving a URL under a secret the old receiver holds is a new endpoint
    wearing the old one's history."""
    headers = _session(client)
    created = _create(client, headers).json()

    body = client.patch(
        f"/api/v1/webhooks/endpoints/{created['id']}",
        json={"url": "https://elsewhere.example.com/hooks"},
        headers=headers,
    ).json()

    assert body["url"] == URL


def test_re_enabling_clears_an_auto_disable(client, db_session):
    headers = _session(client)
    created = _create(client, headers).json()
    from app.models.webhook_endpoint import WebhookEndpoint

    endpoint = db_session.get(WebhookEndpoint, __import__("uuid").UUID(created["id"]))
    endpoint.disabled_at = endpoint.created_at
    endpoint.disabled_reason = "20 consecutive failed deliveries"
    endpoint.consecutive_failures = 20
    db_session.commit()

    body = client.patch(
        f"/api/v1/webhooks/endpoints/{created['id']}",
        json={"is_active": True},
        headers=headers,
    ).json()

    assert body["disabled_at"] is None
    assert body["disabled_reason"] is None
    # Reset too, or the first failure after the fix disables it again.
    assert body["consecutive_failures"] == 0
    assert body["is_deliverable"] is True


# --- secrets --------------------------------------------------------------


def test_the_secret_can_be_revealed_again(client):
    headers = _session(client)
    created = _create(client, headers).json()

    body = client.get(
        f"/api/v1/webhooks/endpoints/{created['id']}/secret", headers=headers
    ).json()

    assert body["secret"] == created["secret"]


def test_revealing_the_secret_is_audited(client, db_session):
    headers = _session(client)
    created = _create(client, headers).json()

    client.get(f"/api/v1/webhooks/endpoints/{created['id']}/secret", headers=headers)

    assert (
        db_session.query(AuditLog)
        .filter(AuditLog.action == AuditAction.WEBHOOK_SECRET_REVEALED.value)
        .count()
        == 1
    )


def test_rotating_replaces_the_secret(client, db_session):
    headers = _session(client)
    created = _create(client, headers).json()

    rotated = client.post(
        f"/api/v1/webhooks/endpoints/{created['id']}/rotate-secret", headers=headers
    ).json()

    assert rotated["secret"] != created["secret"]
    assert rotated["secret"].startswith(SECRET_PREFIX)
    assert (
        db_session.query(AuditLog)
        .filter(AuditLog.action == AuditAction.WEBHOOK_SECRET_ROTATED.value)
        .count()
        == 1
    )


def test_rotating_a_missing_endpoints_secret_is_a_404(client):
    headers = _session(client)

    response = client.post(
        "/api/v1/webhooks/endpoints/11111111-1111-1111-1111-111111111111/rotate-secret",
        headers=headers,
    )

    assert response.status_code == 404


def test_reveals_are_rate_limited(client):
    from app.api.deps_rate_limit import webhook_secret_rate_limit

    headers = _session(client)
    created = _create(client, headers).json()
    rate_limiter.reset()

    path = f"/api/v1/webhooks/endpoints/{created['id']}/secret"
    for _ in range(webhook_secret_rate_limit.limit):
        assert client.get(path, headers=headers).status_code == 200

    assert client.get(path, headers=headers).status_code == 429


# --- the test ping --------------------------------------------------------


def test_a_ping_queues_a_delivery(client):
    headers = _session(client)
    created = _create(client, headers).json()

    response = client.post(
        f"/api/v1/webhooks/endpoints/{created['id']}/ping", headers=headers
    )

    assert response.status_code == 202
    body = response.json()
    assert body["event_type"] == WebhookEventType.PING.value
    assert body["status"] == DeliveryStatus.PENDING.value
    assert body["attempts"] == 0


def test_a_ping_reaches_a_switched_off_endpoint(client):
    """Testing an endpoint you have just re-enabled is the point of a test."""
    headers = _session(client)
    created = _create(client, headers).json()
    client.patch(
        f"/api/v1/webhooks/endpoints/{created['id']}",
        json={"is_active": False},
        headers=headers,
    )

    response = client.post(
        f"/api/v1/webhooks/endpoints/{created['id']}/ping", headers=headers
    )

    assert response.status_code == 202


def test_pinging_a_missing_endpoint_is_a_404(client):
    headers = _session(client)

    response = client.post(
        "/api/v1/webhooks/endpoints/11111111-1111-1111-1111-111111111111/ping",
        headers=headers,
    )

    assert response.status_code == 404


# --- the delivery log -----------------------------------------------------


def test_the_delivery_log_lists_this_accounts_deliveries(client):
    headers = _session(client)
    created = _create(client, headers).json()
    client.post(f"/api/v1/webhooks/endpoints/{created['id']}/ping", headers=headers)

    body = client.get("/api/v1/webhooks/deliveries", headers=headers).json()

    assert len(body) == 1
    assert body[0]["endpoint_id"] == created["id"]
    # The payload is deliberately not in the shape: it is a copy of data the
    # account can already read, and would make this the widest response in
    # the product for no question it answers.
    assert "payload" not in body[0]


def test_the_delivery_log_can_be_filtered_to_one_endpoint(client, db_session):
    headers = _session(client)
    _subscribe(db_session)
    first = _create(client, headers).json()
    second = _create(client, headers, url="https://second.example.com/hooks").json()
    client.post(f"/api/v1/webhooks/endpoints/{first['id']}/ping", headers=headers)
    client.post(f"/api/v1/webhooks/endpoints/{second['id']}/ping", headers=headers)

    body = client.get(
        f"/api/v1/webhooks/deliveries?endpoint_id={second['id']}", headers=headers
    ).json()

    assert [row["endpoint_id"] for row in body] == [second["id"]]


def test_the_delivery_log_never_shows_another_accounts_rows(client):
    owner = _session(client)
    created = _create(client, owner).json()
    client.post(f"/api/v1/webhooks/endpoints/{created['id']}/ping", headers=owner)
    stranger = _session(client, email="stranger@example.com")

    assert client.get("/api/v1/webhooks/deliveries", headers=stranger).json() == []


def test_an_oversized_page_is_refused(client):
    headers = _session(client)

    assert client.get("/api/v1/webhooks/deliveries?limit=5000", headers=headers).status_code == 422


# --- deletion -------------------------------------------------------------


def test_deleting_an_endpoint_removes_it_and_its_queue(client, db_session):
    headers = _session(client)
    created = _create(client, headers).json()
    client.post(f"/api/v1/webhooks/endpoints/{created['id']}/ping", headers=headers)

    assert (
        client.delete(f"/api/v1/webhooks/endpoints/{created['id']}", headers=headers).status_code
        == 204
    )
    assert client.get("/api/v1/webhooks/endpoints", headers=headers).json() == []
    assert client.get("/api/v1/webhooks/deliveries", headers=headers).json() == []


def test_deleting_is_audited(client, db_session):
    headers = _session(client)
    created = _create(client, headers).json()

    client.delete(f"/api/v1/webhooks/endpoints/{created['id']}", headers=headers)

    assert (
        db_session.query(AuditLog)
        .filter(AuditLog.action == AuditAction.WEBHOOK_ENDPOINT_DELETED.value)
        .count()
        == 1
    )


def test_deleting_someone_elses_endpoint_is_a_404(client):
    owner = _session(client)
    created = _create(client, owner).json()
    stranger = _session(client, email="stranger@example.com")

    assert (
        client.delete(
            f"/api/v1/webhooks/endpoints/{created['id']}", headers=stranger
        ).status_code
        == 404
    )


# --- credentials ----------------------------------------------------------


def test_an_api_key_cannot_manage_webhooks(client):
    """A leaked key must not be able to point events at a server the
    attacker controls, nor read the secret that would let it forge them."""
    headers = _session(client)
    key = client.post(
        "/api/v1/api-keys", json={"name": "CI"}, headers=headers
    ).json()["key"]
    key_headers = {"Authorization": f"Bearer {key}"}

    assert client.get("/api/v1/webhooks/endpoints", headers=key_headers).status_code == 401
    assert _create(client, key_headers).status_code == 401


# --- the events the product itself emits ----------------------------------


def test_creating_an_api_key_queues_a_delivery(client):
    headers = _session(client)
    created = _create(
        client, headers, event_types=[WebhookEventType.API_KEY_CREATED.value]
    ).json()

    client.post("/api/v1/api-keys", json={"name": "CI"}, headers=headers)

    body = client.get("/api/v1/webhooks/deliveries", headers=headers).json()
    assert [row["event_type"] for row in body] == [WebhookEventType.API_KEY_CREATED.value]
    assert body[0]["endpoint_id"] == created["id"]


def test_revoking_an_api_key_queues_a_delivery(client):
    headers = _session(client)
    _create(client, headers, event_types=[WebhookEventType.API_KEY_REVOKED.value])
    key_id = client.post(
        "/api/v1/api-keys", json={"name": "CI"}, headers=headers
    ).json()["id"]

    client.delete(f"/api/v1/api-keys/{key_id}", headers=headers)

    body = client.get("/api/v1/webhooks/deliveries", headers=headers).json()
    assert [row["event_type"] for row in body] == [WebhookEventType.API_KEY_REVOKED.value]

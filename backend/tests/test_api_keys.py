"""HTTP surface of API key management and key-authenticated access."""

from app.api.deps_rate_limit import api_key_create_rate_limit
from app.core.entitlements import FREE_PLAN, PRO_PLAN
from app.core.rate_limit import rate_limiter
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.usage_event import EventName, UsageEvent
from app.models.user import User
from app.services.api_key_service import KEY_PREFIX

EMAIL = "keys-api@example.com"
PASSWORD = "s3cret-password"


def _session(client, email=EMAIL, password=PASSWORD):
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Key Holder"},
    )
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _create_key(client, headers, name="CI deploy", **body):
    return client.post("/api/v1/api-keys", json={"name": name, **body}, headers=headers)


def _subscribe(db_session, email=EMAIL):
    """Put the account on the paid plan, for tests that need its ceilings.

    The free tier's key allowance is deliberately small, so anything
    exercising a limit *other* than the plan's — the rate limiter, the hard
    backstop — has to buy its way past it first.
    """
    user = db_session.query(User).filter(User.email == email).one()
    db_session.add(
        Subscription(user_id=user.id, status=SubscriptionStatus.ACTIVE.value)
    )
    db_session.commit()
    return user


def test_creating_a_key_requires_authentication(client):
    assert client.post("/api/v1/api-keys", json={"name": "CI"}).status_code == 401


def test_creation_returns_the_secret_once(client):
    headers = _session(client)

    response = _create_key(client, headers)

    assert response.status_code == 201
    body = response.json()
    assert body["key"].startswith(KEY_PREFIX)
    assert body["name"] == "CI deploy"
    assert body["is_active"] is True
    assert body["revoked_at"] is None

    # And never again: the listing carries metadata only.
    listed = client.get("/api/v1/api-keys", headers=headers).json()
    assert [entry["id"] for entry in listed] == [body["id"]]
    assert "key" not in listed[0]
    assert body["key"] not in str(listed)


def test_creation_rejects_a_blank_name(client):
    headers = _session(client)

    assert _create_key(client, headers, name="   ").status_code == 422


def test_creation_rejects_an_expiry_beyond_the_maximum(client):
    headers = _session(client)

    assert _create_key(client, headers, expires_in_days=10_000).status_code == 422


def test_creation_stops_at_the_free_plan_ceiling(client):
    headers = _session(client)
    for index in range(FREE_PLAN.max_active_api_keys):
        assert _create_key(client, headers, name=f"Key {index}").status_code == 201

    response = _create_key(client, headers, name="One too many")

    assert response.status_code == 409
    # The refusal names the plan and the number, so the reader knows whether
    # to revoke something or to upgrade.
    detail = response.json()["detail"]
    assert FREE_PLAN.display_name in detail
    assert str(FREE_PLAN.max_active_api_keys) in detail


def test_a_paid_plan_raises_the_ceiling(client, db_session):
    headers = _session(client)
    _subscribe(db_session)

    for index in range(FREE_PLAN.max_active_api_keys + 1):
        rate_limiter.reset()
        assert _create_key(client, headers, name=f"Key {index}").status_code == 201


def test_creation_stops_at_the_paid_plan_ceiling(client, db_session):
    headers = _session(client)
    _subscribe(db_session)

    for index in range(PRO_PLAN.max_active_api_keys):
        # The per-minute rate limit is lower than the per-account ceiling, so
        # it has to be stepped over to reach the limit under test here. The
        # rate limit gets its own test below.
        rate_limiter.reset()
        assert _create_key(client, headers, name=f"Key {index}").status_code == 201

    rate_limiter.reset()
    response = _create_key(client, headers, name="One too many")

    assert response.status_code == 409


def test_revoking_a_key_frees_a_slot_under_the_ceiling(client):
    headers = _session(client)
    first = _create_key(client, headers, name="First").json()
    for index in range(FREE_PLAN.max_active_api_keys - 1):
        _create_key(client, headers, name=f"Key {index}")
    assert _create_key(client, headers, name="Blocked").status_code == 409

    client.delete(f"/api/v1/api-keys/{first['id']}", headers=headers)

    # Revoked keys stay listed but stop counting against the plan, which is
    # what makes the ceiling something an account can manage rather than a
    # one-way door.
    assert _create_key(client, headers, name="Now allowed").status_code == 201


def test_creation_is_rate_limited(client, db_session):
    headers = _session(client)
    # On the paid plan, so the ceiling under test is the rate limiter rather
    # than the free tier's much lower key allowance.
    _subscribe(db_session)

    statuses = [
        _create_key(client, headers, name=f"Key {index}").status_code
        for index in range(api_key_create_rate_limit.limit + 1)
    ]

    assert statuses[-1] == 429
    assert statuses[:-1] == [201] * api_key_create_rate_limit.limit


def test_a_key_authenticates_the_api(client):
    headers = _session(client)
    raw_key = _create_key(client, headers).json()["key"]

    response = client.get("/api/v1/users/me", headers={"X-API-Key": raw_key})

    assert response.status_code == 200
    assert response.json()["email"] == EMAIL


def test_a_key_is_also_accepted_as_a_bearer_token(client):
    headers = _session(client)
    raw_key = _create_key(client, headers).json()["key"]

    response = client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {raw_key}"}
    )

    assert response.status_code == 200


def test_an_unknown_key_is_rejected(client):
    _session(client)

    response = client.get(
        "/api/v1/users/me", headers={"X-API-Key": f"{KEY_PREFIX}nope"}
    )

    assert response.status_code == 401


def test_a_key_cannot_manage_keys(client):
    """A leaked key must not be able to mint more or revoke the real ones."""
    headers = _session(client)
    raw_key = _create_key(client, headers).json()["key"]
    key_headers = {"X-API-Key": raw_key}

    assert client.get("/api/v1/api-keys", headers=key_headers).status_code == 401
    assert _create_key(client, key_headers, name="Escalation").status_code == 401


def test_a_key_can_read_usage_and_ingest_events(client):
    headers = _session(client)
    raw_key = _create_key(client, headers).json()["key"]
    key_headers = {"X-API-Key": raw_key}

    ingest = client.post(
        "/api/v1/analytics/events",
        json={"events": [{"name": "integration.synced", "properties": {}}]},
        headers=key_headers,
    )
    usage = client.get("/api/v1/analytics/me", headers=key_headers)

    assert ingest.status_code == 202
    assert usage.status_code == 200
    assert usage.json()["events_by_name"]["integration.synced"] == 1


def test_revocation_stops_the_key_working(client):
    headers = _session(client)
    created = _create_key(client, headers).json()

    revoked = client.delete(f"/api/v1/api-keys/{created['id']}", headers=headers)

    assert revoked.status_code == 204
    assert (
        client.get("/api/v1/users/me", headers={"X-API-Key": created["key"]}).status_code
        == 401
    )


def test_revocation_is_idempotent_over_http(client):
    headers = _session(client)
    created = _create_key(client, headers).json()

    first = client.delete(f"/api/v1/api-keys/{created['id']}", headers=headers)
    second = client.delete(f"/api/v1/api-keys/{created['id']}", headers=headers)

    assert (first.status_code, second.status_code) == (204, 204)


def test_a_revoked_key_stays_listed(client):
    headers = _session(client)
    created = _create_key(client, headers).json()
    client.delete(f"/api/v1/api-keys/{created['id']}", headers=headers)

    listed = client.get("/api/v1/api-keys", headers=headers).json()

    assert len(listed) == 1
    assert listed[0]["revoked_at"] is not None
    assert listed[0]["is_active"] is False


def test_keys_are_scoped_to_their_account(client):
    mine = _session(client)
    created = _create_key(client, mine, name="Mine").json()
    theirs = _session(client, email="intruder@example.com")

    assert client.get("/api/v1/api-keys", headers=theirs).json() == []
    assert (
        client.delete(f"/api/v1/api-keys/{created['id']}", headers=theirs).status_code
        == 404
    )


def test_revoking_an_unknown_key_is_a_404(client):
    headers = _session(client)

    response = client.delete(
        "/api/v1/api-keys/00000000-0000-0000-0000-000000000000", headers=headers
    )

    assert response.status_code == 404


def test_key_lifecycle_is_recorded_as_usage_events(client, db_session):
    headers = _session(client)
    created = _create_key(client, headers).json()
    client.delete(f"/api/v1/api-keys/{created['id']}", headers=headers)

    recorded = {event.name for event in db_session.query(UsageEvent).all()}

    assert EventName.API_KEY_CREATED.value in recorded
    assert EventName.API_KEY_REVOKED.value in recorded


def test_deleting_the_account_revokes_access_by_key(client):
    headers = _session(client)
    raw_key = _create_key(client, headers).json()["key"]

    client.request(
        "DELETE", "/api/v1/users/me", json={"password": PASSWORD}, headers=headers
    )

    assert client.get("/api/v1/users/me", headers={"X-API-Key": raw_key}).status_code == 401

"""HTTP surface for session management.

The service tests (tests/test_session_service.py) assert what stops a token
working. This file asserts the parts that only exist at the edge: who is
allowed to ask, what is safe to show back, and what a 404 must not reveal.
"""

import uuid

import pytest

PASSWORD = "s3cret-password"


def _register(client, email):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "full_name": "Session User"},
    )
    assert response.status_code == 201
    return response


def _login(client, email):
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200
    return response.json()


def _account(client, email="sessions-api@example.com"):
    _register(client, email)
    return _login(client, email)


def _auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture
def tokens(client):
    return _account(client)


def test_listing_requires_authentication(client):
    assert client.get("/api/v1/sessions").status_code == 401


def test_login_appears_in_the_session_list(client, tokens):
    response = client.get("/api/v1/sessions", headers=_auth(tokens))

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["is_active"] is True
    assert body[0]["revoked_at"] is None


def test_the_list_never_carries_token_material(client, tokens):
    body = client.get("/api/v1/sessions", headers=_auth(tokens)).json()

    # A session is identified by where and when it signed in; there is
    # nothing to gain from exposing any part of the secret.
    assert "token_hash" not in body[0]
    assert tokens["refresh_token"] not in str(body)


def test_the_list_records_the_calling_client(client, tokens):
    body = client.get("/api/v1/sessions", headers=_auth(tokens)).json()

    # TestClient sends a User-Agent; the value is decoration, its presence
    # is what lets someone recognise a device.
    assert body[0]["user_agent"]
    assert body[0]["ip_address"]


def test_each_login_is_its_own_session(client):
    _register(client, "multi@example.com")
    first = _login(client, "multi@example.com")
    _login(client, "multi@example.com")

    body = client.get("/api/v1/sessions", headers=_auth(first)).json()

    assert len(body) == 2


def test_an_account_sees_only_its_own_sessions(client):
    _register(client, "mine@example.com")
    _register(client, "theirs@example.com")
    mine = _login(client, "mine@example.com")
    _login(client, "theirs@example.com")

    body = client.get("/api/v1/sessions", headers=_auth(mine)).json()

    assert len(body) == 1


def test_revoking_a_session_stops_its_refresh_token(client):
    _register(client, "revoke-me@example.com")
    victim = _login(client, "revoke-me@example.com")
    caller = _login(client, "revoke-me@example.com")

    # Listing is newest-first, so the victim's session is the older one.
    listed = client.get("/api/v1/sessions", headers=_auth(caller)).json()
    assert len(listed) == 2
    target = listed[1]

    response = client.delete(f"/api/v1/sessions/{target['id']}", headers=_auth(caller))

    assert response.status_code == 204
    assert (
        client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": victim["refresh_token"]},
        ).status_code
        == 401
    )
    # The caller's own session is untouched.
    assert (
        client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": caller["refresh_token"]},
        ).status_code
        == 200
    )


def test_revoking_an_unknown_session_is_a_404(client, tokens):
    response = client.delete(
        f"/api/v1/sessions/{uuid.uuid4()}", headers=_auth(tokens)
    )

    assert response.status_code == 404


def test_cannot_revoke_another_accounts_session(client):
    _register(client, "attacker@example.com")
    _register(client, "victim@example.com")
    attacker = _login(client, "attacker@example.com")
    victim = _login(client, "victim@example.com")

    victim_session = client.get("/api/v1/sessions", headers=_auth(victim)).json()[0]

    response = client.delete(
        f"/api/v1/sessions/{victim_session['id']}", headers=_auth(attacker)
    )

    # 404, not 403: confirming the id exists would leak another account's
    # session ids to anyone willing to guess.
    assert response.status_code == 404
    assert (
        client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": victim["refresh_token"]},
        ).status_code
        == 200
    )


def test_revoked_sessions_stay_in_the_list(client, tokens):
    listed = client.get("/api/v1/sessions", headers=_auth(tokens)).json()
    client.delete(f"/api/v1/sessions/{listed[0]['id']}", headers=_auth(tokens))

    after = client.get("/api/v1/sessions", headers=_auth(tokens)).json()

    # "That device was signed out on the 3rd" has to stay answerable.
    assert len(after) == 1
    assert after[0]["is_active"] is False
    assert after[0]["revoked_at"] is not None


def test_revoke_all_signs_out_every_device(client):
    _register(client, "everywhere@example.com")
    first = _login(client, "everywhere@example.com")
    second = _login(client, "everywhere@example.com")

    response = client.post(
        "/api/v1/sessions/revoke-all", headers=_auth(first), json={}
    )

    assert response.status_code == 200
    assert response.json()["revoked"] == 2
    for tokens in (first, second):
        assert (
            client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": tokens["refresh_token"]},
            ).status_code
            == 401
        )


def test_revoke_all_can_spare_the_caller(client):
    _register(client, "spare-me@example.com")
    keep = _login(client, "spare-me@example.com")
    other = _login(client, "spare-me@example.com")

    response = client.post(
        "/api/v1/sessions/revoke-all",
        headers=_auth(keep),
        json={"keep_current_refresh_token": keep["refresh_token"]},
    )

    assert response.json()["revoked"] == 1
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": keep["refresh_token"]}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": other["refresh_token"]}
        ).status_code
        == 401
    )


def test_revoke_all_ignores_a_token_from_another_account(client):
    _register(client, "a@example.com")
    _register(client, "b@example.com")
    caller = _login(client, "a@example.com")
    stranger = _login(client, "b@example.com")

    response = client.post(
        "/api/v1/sessions/revoke-all",
        headers=_auth(caller),
        json={"keep_current_refresh_token": stranger["refresh_token"]},
    )

    # Sparing nothing is the safe direction to fail in, and the stranger's
    # own session is untouched either way.
    assert response.json()["revoked"] == 1
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": stranger["refresh_token"]}
        ).status_code
        == 200
    )


def test_revoke_all_requires_authentication(client):
    assert client.post("/api/v1/sessions/revoke-all", json={}).status_code == 401


def test_an_api_key_cannot_manage_sessions(client, tokens):
    """A leaked key must not be able to lock the owner out.

    Same bargain the api-keys routes make: a key can read and write the
    product, but it cannot touch the credentials that would let the owner
    take the account back.
    """
    created = client.post(
        "/api/v1/api-keys",
        headers=_auth(tokens),
        json={"name": "CI", "expires_in_days": None},
    )
    assert created.status_code == 201
    raw_key = created.json()["key"]

    assert client.get("/api/v1/sessions", headers={"X-API-Key": raw_key}).status_code == 401
    assert (
        client.post(
            "/api/v1/sessions/revoke-all",
            headers={"X-API-Key": raw_key},
            json={},
        ).status_code
        == 401
    )

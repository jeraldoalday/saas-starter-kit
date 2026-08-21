from app.models.api_key import ApiKey
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.token import OneTimeToken, TokenPurpose
from app.models.usage_event import UsageEvent
from app.models.user import User
from app.services.api_key_service import create_api_key
from app.services.token_service import issue_token

EMAIL = "account@example.com"
PASSWORD = "s3cret-password"


def _auth(client, email=EMAIL, password=PASSWORD):
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Data Subject"},
    )
    login = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_export_requires_authentication(client):
    assert client.get("/api/v1/users/me/export").status_code == 401


def test_export_returns_account_data(client):
    headers = _auth(client)

    response = client.get("/api/v1/users/me/export", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["account"]["email"] == EMAIL
    assert body["account"]["full_name"] == "Data Subject"
    assert body["account"]["role"] == "member"


def test_export_excludes_credentials(client):
    headers = _auth(client)

    body = client.get("/api/v1/users/me/export", headers=headers).json()

    serialized = str(body)
    assert "hashed_password" not in serialized
    assert "token_hash" not in serialized


def test_export_includes_subscription_when_present(client, db_session):
    headers = _auth(client)
    user = db_session.query(User).filter(User.email == EMAIL).one()
    db_session.add(
        Subscription(
            user_id=user.id,
            stripe_customer_id="cus_export",
            status=SubscriptionStatus.ACTIVE.value,
        )
    )
    db_session.commit()

    body = client.get("/api/v1/users/me/export", headers=headers).json()

    assert body["subscription"]["stripe_customer_id"] == "cus_export"


def test_export_handles_no_subscription(client):
    headers = _auth(client)

    body = client.get("/api/v1/users/me/export", headers=headers).json()

    assert body["subscription"] is None


def test_delete_requires_the_correct_password(client):
    headers = _auth(client)

    response = client.request(
        "DELETE",
        "/api/v1/users/me",
        json={"password": "wrong-password"},
        headers=headers,
    )

    assert response.status_code == 403


def test_delete_removes_the_account(client, db_session):
    headers = _auth(client)

    response = client.request(
        "DELETE", "/api/v1/users/me", json={"password": PASSWORD}, headers=headers
    )

    assert response.status_code == 204
    assert db_session.query(User).filter(User.email == EMAIL).first() is None


def test_deleted_user_cannot_log_in(client):
    headers = _auth(client)
    client.request(
        "DELETE", "/api/v1/users/me", json={"password": PASSWORD}, headers=headers
    )

    response = client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )

    assert response.status_code == 401


def test_delete_cleans_up_tokens(client, db_session):
    headers = _auth(client)
    user = db_session.query(User).filter(User.email == EMAIL).one()
    issue_token(db_session, user, TokenPurpose.PASSWORD_RESET)
    assert db_session.query(OneTimeToken).count() > 0

    client.request(
        "DELETE", "/api/v1/users/me", json={"password": PASSWORD}, headers=headers
    )

    assert db_session.query(OneTimeToken).count() == 0


def test_delete_cleans_up_subscription_row(client, db_session):
    headers = _auth(client)
    user = db_session.query(User).filter(User.email == EMAIL).one()
    db_session.add(
        Subscription(user_id=user.id, status=SubscriptionStatus.CANCELED.value)
    )
    db_session.commit()

    client.request(
        "DELETE", "/api/v1/users/me", json={"password": PASSWORD}, headers=headers
    )

    assert db_session.query(Subscription).count() == 0


def test_delete_is_blocked_while_subscription_is_active(client, db_session):
    """Deleting our row wouldn't stop Stripe billing them."""
    headers = _auth(client)
    user = db_session.query(User).filter(User.email == EMAIL).one()
    db_session.add(
        Subscription(user_id=user.id, status=SubscriptionStatus.ACTIVE.value)
    )
    db_session.commit()

    response = client.request(
        "DELETE", "/api/v1/users/me", json={"password": PASSWORD}, headers=headers
    )

    assert response.status_code == 409
    assert "cancel" in response.json()["detail"].lower()
    assert db_session.query(User).filter(User.email == EMAIL).first() is not None


def test_delete_allowed_once_subscription_is_canceled(client, db_session):
    headers = _auth(client)
    user = db_session.query(User).filter(User.email == EMAIL).one()
    db_session.add(
        Subscription(user_id=user.id, status=SubscriptionStatus.CANCELED.value)
    )
    db_session.commit()

    response = client.request(
        "DELETE", "/api/v1/users/me", json={"password": PASSWORD}, headers=headers
    )

    assert response.status_code == 204


def test_delete_requires_authentication(client):
    response = client.request(
        "DELETE", "/api/v1/users/me", json={"password": PASSWORD}
    )

    assert response.status_code == 401


def test_export_includes_recorded_usage_events(client, db_session):
    """Behaviour recorded about someone is data held about them."""
    headers = _auth(client)

    body = client.get("/api/v1/users/me/export", headers=headers).json()

    names = [event["name"] for event in body["usage_events"]]
    assert "user.registered" in names
    assert "user.logged_in" in names


def test_delete_erases_the_accounts_usage_events(client, db_session):
    headers = _auth(client)
    user_id = db_session.query(User).filter(User.email == EMAIL).one().id

    client.request(
        "DELETE", "/api/v1/users/me", json={"password": PASSWORD}, headers=headers
    )

    assert (
        db_session.query(UsageEvent).filter(UsageEvent.user_id == user_id).count() == 0
    )


def test_export_lists_api_keys_without_their_secrets(client, db_session):
    headers = _auth(client, email="keys-export@example.com")
    user = db_session.query(User).filter(User.email == "keys-export@example.com").one()
    api_key, raw_key = create_api_key(db_session, user, name="CI deploy")

    body = client.get("/api/v1/users/me/export", headers=headers).json()

    exported = body["api_keys"]
    assert [entry["name"] for entry in exported] == ["CI deploy"]
    assert exported[0]["prefix"] == api_key.prefix
    # The digest is a credential, like the password hash, and the raw secret
    # was never stored — neither may appear in a file the user carries away.
    serialized = str(body)
    assert raw_key not in serialized
    assert api_key.token_hash not in serialized


def test_export_reports_no_keys_as_an_empty_list(client):
    headers = _auth(client, email="no-keys@example.com")

    assert client.get("/api/v1/users/me/export", headers=headers).json()["api_keys"] == []


def test_deleting_an_account_destroys_its_api_keys(client, db_session):
    headers = _auth(client, email="key-erasure@example.com")
    user = db_session.query(User).filter(User.email == "key-erasure@example.com").one()
    create_api_key(db_session, user, name="CI deploy")

    response = client.request(
        "DELETE", "/api/v1/users/me", json={"password": PASSWORD}, headers=headers
    )

    assert response.status_code == 204
    assert db_session.query(ApiKey).count() == 0

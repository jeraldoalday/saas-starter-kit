def _register(client, email="user@example.com", password="s3cret-password"):
    return client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Test User"},
    )


def test_register_rejects_short_password(client):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "short@example.com", "password": "abc123", "full_name": None},
    )
    assert response.status_code == 422


def test_register_rejects_overlong_password(client):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "long@example.com", "password": "a" * 100, "full_name": None},
    )
    assert response.status_code == 422


def test_password_is_hashed_not_stored_plaintext(client, db_session):
    from app.models.user import User

    _register(client, email="hash@example.com")

    user = db_session.query(User).filter(User.email == "hash@example.com").first()
    assert user is not None
    assert user.hashed_password != "s3cret-password"
    assert user.hashed_password.startswith("$2b$")


def test_register_creates_user(client):
    response = _register(client)
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "user@example.com"
    assert "password" not in body
    assert "hashed_password" not in body


def test_register_duplicate_email_conflicts(client):
    _register(client)
    response = _register(client)
    assert response.status_code == 409


def test_login_returns_token_pair(client):
    _register(client)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": "s3cret-password"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]


def test_login_wrong_password_rejected(client):
    _register(client)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 401


def test_me_requires_authentication(client):
    response = client.get("/api/v1/users/me")
    assert response.status_code == 401


def test_me_returns_current_user(client):
    _register(client)
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": "s3cret-password"},
    )
    token = login.json()["access_token"]

    response = client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.json()["email"] == "user@example.com"


def test_refresh_rejects_access_token(client):
    _register(client)
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": "s3cret-password"},
    )
    access_token = login.json()["access_token"]

    response = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": access_token}
    )
    assert response.status_code == 401


def test_refresh_issues_new_access_token(client):
    _register(client)
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": "s3cret-password"},
    )
    refresh_token = login.json()["refresh_token"]

    response = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_registration_stores_the_address_folded(client, db_session):
    from app.models.user import User

    _register(client, email="  Ada.Lovelace@Example.COM  ")

    user = db_session.query(User).filter(User.email == "ada.lovelace@example.com").one()
    assert user.email == "ada.lovelace@example.com"


def test_registering_the_same_address_in_another_case_is_a_conflict(client):
    """One inbox, one account — regardless of which shift key they pressed."""
    _register(client, email="ada@example.com")

    response = _register(client, email="ADA@example.com")

    assert response.status_code == 409


def test_login_is_case_insensitive(client):
    _register(client, email="ada@example.com", password="s3cret-password")

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "Ada@Example.com", "password": "s3cret-password"},
    )

    assert response.status_code == 200
    assert "access_token" in response.json()


def test_password_reset_lookup_is_case_insensitive(client, outbox):
    _register(client, email="ada@example.com")

    client.post("/api/v1/auth/forgot-password", json={"email": "ADA@EXAMPLE.COM"})

    assert outbox.last_to("ada@example.com") is not None

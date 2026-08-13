def _register(client, email="user@example.com", password="s3cret-password"):
    return client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Test User"},
    )


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

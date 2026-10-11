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


def test_register_rejects_a_password_that_clears_the_character_limit_but_not_the_byte_one(
    client,
):
    """72 'é' is 72 characters (passes the schema's max_length) but 144 UTF-8
    bytes (fails bcrypt's real limit) - this must come back as a clean 400,
    not an unhandled 500."""
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "multibyte@example.com", "password": "é" * 72, "full_name": None},
    )
    assert response.status_code == 400
    assert "72 bytes" in response.json()["detail"]


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


def test_deactivated_account_cannot_log_in(client, db_session):
    from app.models.user import User

    _register(client, email="off@example.com", password="s3cret-password")
    user = db_session.query(User).filter(User.email == "off@example.com").one()
    user.is_active = False
    db_session.commit()

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "off@example.com", "password": "s3cret-password"},
    )

    assert response.status_code == 403
    assert "deactivated" in response.json()["detail"].lower()


def test_deactivated_account_gets_no_tokens(client, db_session):
    """A refresh token would outlive the deactivation by days."""
    from app.models.user import User

    _register(client, email="off2@example.com", password="s3cret-password")
    user = db_session.query(User).filter(User.email == "off2@example.com").one()
    user.is_active = False
    db_session.commit()

    body = client.post(
        "/api/v1/auth/login",
        json={"email": "off2@example.com", "password": "s3cret-password"},
    ).json()

    assert "access_token" not in body
    assert "refresh_token" not in body


def test_wrong_password_on_a_deactivated_account_still_reads_as_wrong_password(
    client, db_session
):
    """Deactivation must not become an oracle for guessing passwords."""
    from app.models.user import User

    _register(client, email="off3@example.com", password="s3cret-password")
    user = db_session.query(User).filter(User.email == "off3@example.com").one()
    user.is_active = False
    db_session.commit()

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "off3@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 401


class TestRefreshRotation:
    """Refreshing retires the token it was given.

    Rotation is what closes the window on a captured refresh token: it stops
    working as soon as the legitimate client next refreshes, instead of
    lasting the full REFRESH_TOKEN_EXPIRE_DAYS.
    """

    @staticmethod
    def _login(client, email="rotate@example.com"):
        _register(client, email=email)
        response = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "s3cret-password"},
        )
        assert response.status_code == 200
        return response.json()["refresh_token"]

    def test_refresh_returns_a_different_refresh_token(self, client):
        original = self._login(client)

        response = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": original}
        )

        assert response.status_code == 200
        assert response.json()["refresh_token"] != original

    def test_the_previous_refresh_token_stops_working(self, client):
        original = self._login(client)
        client.post("/api/v1/auth/refresh", json={"refresh_token": original})

        replay = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": original}
        )

        assert replay.status_code == 401

    def test_the_rotated_token_keeps_working(self, client):
        original = self._login(client)
        rotated = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": original}
        ).json()["refresh_token"]

        again = client.post("/api/v1/auth/refresh", json={"refresh_token": rotated})

        assert again.status_code == 200

    def test_a_signature_alone_is_not_enough(self, client, db_session):
        """A correctly signed token with no session behind it is refused.

        This is the property the whole auth_sessions table exists for: before
        it, anything bearing a valid signature refreshed indefinitely.
        """
        from app.core.security import create_refresh_token
        from app.models.user import User

        _register(client, email="forged@example.com")
        user = db_session.query(User).filter(User.email == "forged@example.com").one()
        forged = create_refresh_token(str(user.id))

        response = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": forged}
        )

        assert response.status_code == 401


class TestLogout:
    @staticmethod
    def _login(client, email="logout@example.com"):
        _register(client, email=email)
        return client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "s3cret-password"},
        ).json()["refresh_token"]

    def test_logout_ends_the_session(self, client):
        refresh_token = self._login(client)

        response = client.post(
            "/api/v1/auth/logout", json={"refresh_token": refresh_token}
        )

        assert response.status_code == 200
        # The whole point: the token is dead afterwards.
        assert (
            client.post(
                "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
            ).status_code
            == 401
        )

    def test_logout_is_idempotent(self, client):
        refresh_token = self._login(client)
        client.post("/api/v1/auth/logout", json={"refresh_token": refresh_token})

        again = client.post(
            "/api/v1/auth/logout", json={"refresh_token": refresh_token}
        )

        assert again.status_code == 200

    def test_logout_does_not_reveal_whether_a_token_was_real(self, client):
        real = self._login(client)

        known = client.post("/api/v1/auth/logout", json={"refresh_token": real})
        unknown = client.post(
            "/api/v1/auth/logout", json={"refresh_token": "not-a-real-token"}
        )

        # Differing answers would turn this into an oracle for testing
        # captured tokens against, from an unauthenticated endpoint.
        assert known.status_code == unknown.status_code
        assert known.json() == unknown.json()

    def test_logout_leaves_other_sessions_alone(self, client):
        first = self._login(client, email="two-devices@example.com")
        second = client.post(
            "/api/v1/auth/login",
            json={"email": "two-devices@example.com", "password": "s3cret-password"},
        ).json()["refresh_token"]

        client.post("/api/v1/auth/logout", json={"refresh_token": first})

        # Signing out the laptop must not sign out the phone.
        assert (
            client.post(
                "/api/v1/auth/refresh", json={"refresh_token": second}
            ).status_code
            == 200
        )

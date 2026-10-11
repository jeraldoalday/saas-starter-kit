import re

from app.core.security import verify_password
from app.models.token import OneTimeToken, TokenPurpose
from app.models.user import User
from app.services.token_service import issue_token

EMAIL = "reset@example.com"
OLD_PASSWORD = "old-s3cret-password"
NEW_PASSWORD = "new-s3cret-password"


def _register(client, email=EMAIL, password=OLD_PASSWORD):
    return client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )


def _reset_token(outbox, email=EMAIL) -> str:
    message = outbox.last_to(email)
    assert message is not None, "no reset email was sent"
    match = re.search(r"reset-password\?token=([\w\-]+)", message.text_body)
    assert match, f"no reset token in body: {message.text_body}"
    return match.group(1)


def test_forgot_password_sends_email(client, outbox):
    _register(client)
    outbox.clear()

    response = client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})

    assert response.status_code == 200
    assert outbox.last_to(EMAIL) is not None


def test_forgot_password_does_not_reveal_unknown_addresses(client, outbox):
    _register(client)
    outbox.clear()

    known = client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
    unknown = client.post(
        "/api/v1/auth/forgot-password", json={"email": "ghost@example.com"}
    )

    # Identical responses, so the endpoint can't be used to enumerate accounts.
    assert known.status_code == unknown.status_code
    assert known.json() == unknown.json()
    assert outbox.last_to("ghost@example.com") is None


def test_reset_changes_the_password(client, outbox, db_session):
    _register(client)
    outbox.clear()
    client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
    token = _reset_token(outbox)

    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 200
    user = db_session.query(User).filter(User.email == EMAIL).one()
    db_session.refresh(user)
    assert verify_password(NEW_PASSWORD, user.hashed_password)
    assert not verify_password(OLD_PASSWORD, user.hashed_password)


def test_new_password_works_for_login(client, outbox):
    _register(client)
    outbox.clear()
    client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
    client.post(
        "/api/v1/auth/reset-password",
        json={"token": _reset_token(outbox), "new_password": NEW_PASSWORD},
    )

    response = client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": NEW_PASSWORD}
    )

    assert response.status_code == 200


def test_old_password_stops_working(client, outbox):
    _register(client)
    outbox.clear()
    client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
    client.post(
        "/api/v1/auth/reset-password",
        json={"token": _reset_token(outbox), "new_password": NEW_PASSWORD},
    )

    response = client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": OLD_PASSWORD}
    )

    assert response.status_code == 401


def test_reset_token_is_single_use(client, outbox):
    _register(client)
    outbox.clear()
    client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
    token = _reset_token(outbox)

    client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": NEW_PASSWORD},
    )
    second = client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "another-s3cret-pw"},
    )

    assert second.status_code == 400


def test_invalid_token_is_rejected(client):
    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": "not-a-real-token", "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 400


def test_short_new_password_is_rejected(client, outbox):
    _register(client)
    outbox.clear()
    client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})

    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": _reset_token(outbox), "new_password": "short"},
    )

    assert response.status_code == 422


def test_reset_rejects_a_password_that_clears_the_character_limit_but_not_the_byte_one(
    client, outbox
):
    """72 'é' is 72 characters (passes the schema's max_length) but 144 UTF-8
    bytes (fails bcrypt's real limit) - this must come back as a clean 400,
    not an unhandled 500."""
    _register(client)
    outbox.clear()
    client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})

    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": _reset_token(outbox), "new_password": "é" * 72},
    )

    assert response.status_code == 400
    assert "72 bytes" in response.json()["detail"]


def test_a_verification_token_cannot_reset_a_password(client, db_session, outbox):
    _register(client)
    user = db_session.query(User).filter(User.email == EMAIL).one()
    verification_raw = issue_token(db_session, user, TokenPurpose.EMAIL_VERIFICATION)

    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": verification_raw, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 400


def test_resetting_invalidates_other_outstanding_tokens(client, outbox, db_session):
    _register(client)
    user = db_session.query(User).filter(User.email == EMAIL).one()

    # Two reset requests: only the newest link should be usable, and once it
    # is spent nothing else should remain live.
    client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
    outbox.clear()
    client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
    newest = _reset_token(outbox)

    client.post(
        "/api/v1/auth/reset-password",
        json={"token": newest, "new_password": NEW_PASSWORD},
    )

    live = (
        db_session.query(OneTimeToken)
        .filter(
            OneTimeToken.user_id == user.id,
            OneTimeToken.purpose == TokenPurpose.PASSWORD_RESET.value,
            OneTimeToken.used_at.is_(None),
        )
        .count()
    )
    assert live == 0


def test_inactive_user_gets_no_reset_email(client, db_session, outbox):
    _register(client)
    user = db_session.query(User).filter(User.email == EMAIL).one()
    user.is_active = False
    db_session.commit()
    outbox.clear()

    client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})

    assert outbox.last_to(EMAIL) is None


class TestResetEndsExistingSessions:
    """Resetting a password signs every device out.

    People reset a password precisely when they think someone else has
    access. While refresh tokens were stateless this did nothing to that
    someone: their token kept minting access tokens through the reset and
    for the rest of its lifetime.
    """

    @staticmethod
    def _login(client, email=EMAIL, password=OLD_PASSWORD) -> str:
        response = client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        assert response.status_code == 200
        return response.json()["refresh_token"]

    @staticmethod
    def _reset(client, outbox, new_password=NEW_PASSWORD):
        outbox.clear()
        client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
        response = client.post(
            "/api/v1/auth/reset-password",
            json={"token": _reset_token(outbox), "new_password": new_password},
        )
        assert response.status_code == 200

    def test_outstanding_refresh_tokens_stop_working(self, client, outbox):
        _register(client)
        refresh_token = self._login(client)

        self._reset(client, outbox)

        assert (
            client.post(
                "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
            ).status_code
            == 401
        )

    def test_every_device_is_signed_out_not_just_one(self, client, outbox):
        _register(client)
        first = self._login(client)
        second = self._login(client)

        self._reset(client, outbox)

        for token in (first, second):
            assert (
                client.post(
                    "/api/v1/auth/refresh", json={"refresh_token": token}
                ).status_code
                == 401
            )

    def test_the_new_password_still_signs_in(self, client, outbox):
        _register(client)
        self._login(client)

        self._reset(client, outbox)

        # Revoking everything must not lock the owner out of their own account.
        assert self._login(client, password=NEW_PASSWORD)

    def test_other_accounts_keep_their_sessions(self, client, outbox):
        _register(client)
        _register(client, email="bystander@example.com")
        bystander = self._login(client, email="bystander@example.com")

        self._reset(client, outbox)

        assert (
            client.post(
                "/api/v1/auth/refresh", json={"refresh_token": bystander}
            ).status_code
            == 200
        )

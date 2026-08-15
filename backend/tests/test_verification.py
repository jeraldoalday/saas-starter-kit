import re

import pytest

from app.core.config import get_settings
from app.models.token import OneTimeToken, TokenPurpose
from app.models.user import User
from app.services import verification_service
from app.services.token_service import consume_token, hash_token, issue_token

EMAIL = "verify@example.com"
PASSWORD = "s3cret-password"


def _register(client, email=EMAIL):
    return client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
    )


def _token_from_outbox(outbox, email=EMAIL) -> str:
    message = outbox.last_to(email)
    assert message is not None, "no verification email was sent"
    match = re.search(r"token=([\w\-]+)", message.text_body)
    assert match, f"no token in email body: {message.text_body}"
    return match.group(1)


def test_registration_sends_verification_email(client, outbox):
    _register(client)

    assert outbox.last_to(EMAIL) is not None


def test_new_user_starts_unverified(client, db_session):
    _register(client)

    user = db_session.query(User).filter(User.email == EMAIL).one()
    assert user.is_verified is False


def test_verifying_with_emailed_token_marks_user_verified(client, outbox, db_session):
    _register(client)
    token = _token_from_outbox(outbox)

    response = client.post("/api/v1/auth/verify-email", json={"token": token})

    assert response.status_code == 200
    user = db_session.query(User).filter(User.email == EMAIL).one()
    db_session.refresh(user)
    assert user.is_verified is True


def test_token_cannot_be_reused(client, outbox):
    _register(client)
    token = _token_from_outbox(outbox)

    client.post("/api/v1/auth/verify-email", json={"token": token})
    second = client.post("/api/v1/auth/verify-email", json={"token": token})

    assert second.status_code == 400


def test_unknown_token_is_rejected(client):
    response = client.post("/api/v1/auth/verify-email", json={"token": "nonsense"})

    assert response.status_code == 400


def test_raw_token_is_not_stored(client, outbox, db_session):
    _register(client)
    token = _token_from_outbox(outbox)

    stored = db_session.query(OneTimeToken).one()
    assert stored.token_hash != token
    assert stored.token_hash == hash_token(token)


def test_issuing_a_new_token_invalidates_the_previous_one(db_session):
    user = User(email="rotate@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()

    first = issue_token(db_session, user, TokenPurpose.EMAIL_VERIFICATION)
    issue_token(db_session, user, TokenPurpose.EMAIL_VERIFICATION)

    assert consume_token(db_session, first, TokenPurpose.EMAIL_VERIFICATION) is None


def test_token_of_the_wrong_purpose_is_rejected(db_session):
    user = User(email="purpose@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()

    raw = issue_token(db_session, user, TokenPurpose.EMAIL_VERIFICATION)

    assert consume_token(db_session, raw, TokenPurpose.PASSWORD_RESET) is None


def test_expired_token_is_rejected(db_session):
    from datetime import timedelta

    user = User(email="expired@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()

    raw = issue_token(
        db_session, user, TokenPurpose.EMAIL_VERIFICATION, ttl=timedelta(seconds=-1)
    )

    assert consume_token(db_session, raw, TokenPurpose.EMAIL_VERIFICATION) is None


def test_already_verified_user_gets_no_further_email(db_session, outbox):
    user = User(email="done@example.com", hashed_password="x", is_verified=True)
    db_session.add(user)
    db_session.commit()

    verification_service.send_verification_email(db_session, user)

    assert outbox.outbox == []


def test_resend_requires_authentication(client):
    assert client.post("/api/v1/auth/resend-verification").status_code == 401


def test_resend_issues_a_fresh_email(client, outbox):
    _register(client)
    login = client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    outbox.clear()

    response = client.post("/api/v1/auth/resend-verification", headers=headers)

    assert response.status_code == 200
    assert outbox.last_to(EMAIL) is not None


@pytest.fixture
def require_verification(monkeypatch):
    monkeypatch.setattr(get_settings(), "require_email_verification", True)


def test_billing_is_gated_on_verification(client, require_verification):
    _register(client)
    login = client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = client.post("/api/v1/billing/checkout", headers=headers)

    assert response.status_code == 403
    assert "verified" in response.json()["detail"].lower()


def test_gate_lets_verified_users_through(client, outbox, require_verification):
    _register(client)
    client.post(
        "/api/v1/auth/verify-email", json={"token": _token_from_outbox(outbox)}
    )
    login = client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = client.post("/api/v1/billing/portal", headers=headers)

    # Past the gate: 409 (no Stripe customer) rather than 403 (unverified).
    assert response.status_code == 409

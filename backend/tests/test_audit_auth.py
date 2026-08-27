"""What the authentication flows leave behind in the audit trail.

The point of these is not that a row exists — it is *which* row, attributed
to whom. A trail that records every refusal anonymously tells an owner
nothing about their own account, and one that records the address a stranger
typed is an inventory of strings nobody asked us to keep.
"""

import re

import pytest

from app.models.audit_log import AuditAction, AuditLog, AuditOutcome
from app.models.user import User

PASSWORD = "s3cret-password"
EMAIL = "audit-auth@example.com"


def _register(client, email=EMAIL):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "full_name": "Trail User"},
    )
    assert response.status_code == 201
    return response.json()


def _login(client, email=EMAIL, password=PASSWORD):
    return client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


def _token_from(outbox, email=EMAIL) -> str:
    message = outbox.last_to(email)
    assert message is not None, "no email was sent"
    match = re.search(r"token=([\w\-]+)", message.text_body)
    assert match, f"no token in body: {message.text_body}"
    return match.group(1)


def _trail(db_session, action=None):
    query = db_session.query(AuditLog)
    if action is not None:
        query = query.filter(AuditLog.action == action.value)
    return query.order_by(AuditLog.created_at).all()


@pytest.fixture
def registered(client, db_session):
    _register(client)
    return db_session.query(User).filter(User.email == EMAIL).one()


def test_registration_opens_the_trail(client, db_session):
    _register(client)

    entries = _trail(db_session, AuditAction.ACCOUNT_REGISTERED)
    assert len(entries) == 1
    assert entries[0].outcome == AuditOutcome.SUCCESS.value


def test_a_successful_login_is_recorded_with_its_origin(
    client, db_session, registered
):
    assert _login(client).status_code == 200

    entry = _trail(db_session, AuditAction.LOGIN)[-1]
    assert entry.user_id == registered.id
    # An unfamiliar sign-in is only recognisable as one if the row says
    # where it came from.
    assert entry.ip_address is not None
    assert entry.user_agent is not None


def test_a_wrong_password_is_recorded_against_the_account(
    client, db_session, registered
):
    assert _login(client, password="wrong-password").status_code == 401

    entry = _trail(db_session, AuditAction.LOGIN_FAILED)[-1]
    # Attributed, so the owner sees the attempt in their own history.
    assert entry.user_id == registered.id
    assert entry.outcome == AuditOutcome.FAILURE.value
    assert entry.context == {"known_account": True}


def test_a_failed_login_for_an_unknown_address_names_nobody(client, db_session):
    assert _login(client, email="nobody@example.com").status_code == 401

    entry = _trail(db_session, AuditAction.LOGIN_FAILED)[-1]
    assert entry.user_id is None
    assert entry.context == {"known_account": False}


def test_the_trail_never_stores_the_address_that_was_tried(client, db_session):
    _login(client, email="typo-of-a-real-customer@example.com", password="hunter2")

    # Storing it would build an inventory of every string typed into an
    # email box, passwords typed into the wrong field included.
    dumped = str([(row.action, row.context) for row in _trail(db_session)])
    assert "typo-of-a-real-customer" not in dumped
    assert "hunter2" not in dumped


def test_a_deactivated_account_records_a_block_not_a_guess(
    client, db_session, registered
):
    registered.is_active = False
    db_session.commit()

    assert _login(client).status_code == 403

    # Somebody signing in with the *right* password to a switched-off
    # account is a different story from somebody guessing.
    assert _trail(db_session, AuditAction.LOGIN_FAILED) == []
    blocked = _trail(db_session, AuditAction.LOGIN_BLOCKED)
    assert len(blocked) == 1
    assert blocked[0].user_id == registered.id


def test_logout_records_the_session_it_ended(client, db_session, registered):
    tokens = _login(client).json()

    response = client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )

    assert response.status_code == 200
    entry = _trail(db_session, AuditAction.LOGOUT)[-1]
    assert entry.user_id == registered.id
    assert "session_id" in entry.context


def test_an_unknown_refresh_token_appends_nothing(client, db_session, registered):
    response = client.post(
        "/api/v1/auth/logout", json={"refresh_token": "not-a-token"}
    )

    # Otherwise an unauthenticated caller could append to the trail at will.
    assert response.status_code == 200
    assert _trail(db_session, AuditAction.LOGOUT) == []


def test_a_reset_request_for_a_known_address_is_attributed(
    client, db_session, registered
):
    response = client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})

    assert response.status_code == 200
    entry = _trail(db_session, AuditAction.PASSWORD_RESET_REQUESTED)[-1]
    assert entry.user_id == registered.id
    assert entry.context == {"known_account": True}


def test_a_reset_request_for_an_unknown_address_answers_the_same_way(
    client, db_session
):
    known = client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
    unknown = client.post(
        "/api/v1/auth/forgot-password", json={"email": "nobody@example.com"}
    )

    # The endpoint must stay an unusable enumeration oracle. Auditing it
    # changes what we store, never what the caller is told.
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


def test_a_completed_reset_explains_the_sign_outs_that_follow(
    client, db_session, registered, outbox
):
    _login(client)
    outbox.clear()
    client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
    token = _token_from(outbox)

    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "an-even-better-password"},
    )

    assert response.status_code == 200
    entry = _trail(db_session, AuditAction.PASSWORD_RESET_COMPLETED)[-1]
    assert entry.user_id == registered.id


def test_a_dud_reset_token_leaves_no_row(client, db_session, registered):
    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": "not-a-token", "new_password": "an-even-better-password"},
    )

    # It names no account, so there is no trail it belongs in.
    assert response.status_code == 400
    assert _trail(db_session, AuditAction.PASSWORD_RESET_COMPLETED) == []


def test_email_verification_is_recorded(client, db_session, registered, outbox):
    response = client.post(
        "/api/v1/auth/verify-email", json={"token": _token_from(outbox)}
    )

    assert response.status_code == 200
    assert len(_trail(db_session, AuditAction.EMAIL_VERIFIED)) == 1

"""Credential, session, admin and lifecycle events in the audit trail.

The authentication half lives in tests/test_audit_auth.py. What is distinct
here is attribution across accounts — an admin acting on somebody else — and
the two lifecycle operations that have to stay in step with the schema: the
data export and the erasure.
"""

import re

import pytest

from app.core.security import hash_password
from app.models.audit_log import AuditAction, AuditLog
from app.models.role import Role
from app.models.user import User

PASSWORD = "s3cret-password"
EMAIL = "audit-account@example.com"


def _register(client, email=EMAIL):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "full_name": "Trail User"},
    )
    assert response.status_code == 201
    return response.json()


def _login(client, email=EMAIL):
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200
    return response.json()


def _auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _trail(db_session, action=None):
    query = db_session.query(AuditLog)
    if action is not None:
        query = query.filter(AuditLog.action == action.value)
    return query.order_by(AuditLog.created_at).all()


def _promote(db_session, email, role: Role) -> User:
    user = db_session.query(User).filter(User.email == email).one()
    user.role = role.value
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def member(client, db_session) -> User:
    _register(client)
    return db_session.query(User).filter(User.email == EMAIL).one()


@pytest.fixture
def tokens(client, member):
    return _login(client)


def test_creating_a_key_records_its_prefix_and_never_its_secret(
    client, db_session, tokens
):
    created = client.post(
        "/api/v1/api-keys", json={"name": "CI deploy"}, headers=_auth(tokens)
    ).json()

    entry = _trail(db_session, AuditAction.API_KEY_CREATED)[-1]
    assert entry.context["prefix"] == created["prefix"]
    assert entry.context["name"] == "CI deploy"
    # The prefix is a display aid; the key itself is not stored anywhere.
    assert created["key"] not in str(entry.context)


def test_revoking_a_key_records_which_one(client, db_session, tokens):
    created = client.post(
        "/api/v1/api-keys", json={"name": "Zapier"}, headers=_auth(tokens)
    ).json()

    client.delete(f"/api/v1/api-keys/{created['id']}", headers=_auth(tokens))

    entry = _trail(db_session, AuditAction.API_KEY_REVOKED)[-1]
    assert entry.context == {"key_id": created["id"]}


def test_a_refused_key_revocation_records_nothing(client, db_session, tokens):
    import uuid as _uuid

    response = client.delete(
        f"/api/v1/api-keys/{_uuid.uuid4()}", headers=_auth(tokens)
    )

    assert response.status_code == 404
    assert _trail(db_session, AuditAction.API_KEY_REVOKED) == []


def test_revoking_one_session_records_which_one(client, db_session, tokens):
    listed = client.get("/api/v1/sessions", headers=_auth(tokens)).json()

    client.delete(f"/api/v1/sessions/{listed[0]['id']}", headers=_auth(tokens))

    entry = _trail(db_session, AuditAction.SESSION_REVOKED)[-1]
    assert entry.context == {"session_id": listed[0]["id"]}


def test_signing_out_everywhere_records_the_count(client, db_session, member):
    tokens = _login(client)
    _login(client)

    response = client.post(
        "/api/v1/sessions/revoke-all", json={}, headers=_auth(tokens)
    )

    assert response.status_code == 200
    entry = _trail(db_session, AuditAction.SESSIONS_REVOKED_ALL)[-1]
    # "I signed out 2 devices" is what makes a later "and one signed
    # straight back in" legible.
    assert entry.context == {"revoked": 2, "kept_current": False}


def test_an_export_is_recorded_against_the_account(client, db_session, tokens):
    assert client.get("/api/v1/users/me/export", headers=_auth(tokens)).status_code == 200

    # The one route that hands the whole account over in a single response.
    assert len(_trail(db_session, AuditAction.ACCOUNT_EXPORTED)) == 1


def test_the_export_carries_the_accounts_own_trail(client, db_session, tokens):
    export = client.get("/api/v1/users/me/export", headers=_auth(tokens)).json()

    actions = [entry["action"] for entry in export["audit_log"]]
    assert AuditAction.LOGIN.value in actions
    assert AuditAction.ACCOUNT_REGISTERED.value in actions


def test_the_export_never_names_the_admin_who_acted(client, db_session, member):
    owner = User(
        email="owner@example.com",
        hashed_password=hash_password(PASSWORD),
        is_active=True,
        is_verified=True,
        role=Role.OWNER.value,
    )
    db_session.add(owner)
    db_session.commit()
    owner_tokens = _login(client, email="owner@example.com")

    client.patch(
        f"/api/v1/admin/users/{member.id}/role",
        json={"role": Role.ADMIN.value},
        headers=_auth(owner_tokens),
    )

    export = client.get(
        "/api/v1/users/me/export", headers=_auth(_login(client))
    ).json()
    changed = [
        entry
        for entry in export["audit_log"]
        if entry["action"] == AuditAction.ROLE_CHANGED.value
    ]

    # "Somebody else did this" rather than who: naming the owner would hand
    # out an internal identifier the subject has no claim to.
    assert len(changed) == 1
    assert changed[0]["by_another_account"] is True
    assert str(owner.id) not in str(export)


def test_a_role_change_lands_in_the_subjects_trail(client, db_session, member):
    _register(client, email="owner2@example.com")
    owner = _promote(db_session, "owner2@example.com", Role.OWNER)
    owner_tokens = _login(client, email="owner2@example.com")

    response = client.patch(
        f"/api/v1/admin/users/{member.id}/role",
        json={"role": Role.ADMIN.value},
        headers=_auth(owner_tokens),
    )

    assert response.status_code == 200
    entry = _trail(db_session, AuditAction.ROLE_CHANGED)[-1]
    assert entry.user_id == member.id
    assert entry.actor_user_id == owner.id
    assert entry.context == {"from": Role.MEMBER.value, "to": Role.ADMIN.value}


def test_a_role_set_to_what_it_already_was_records_nothing(
    client, db_session, member
):
    _register(client, email="owner3@example.com")
    _promote(db_session, "owner3@example.com", Role.OWNER)
    owner_tokens = _login(client, email="owner3@example.com")

    client.patch(
        f"/api/v1/admin/users/{member.id}/role",
        json={"role": Role.MEMBER.value},
        headers=_auth(owner_tokens),
    )

    # A trail full of no-ops is a trail nobody reads.
    assert _trail(db_session, AuditAction.ROLE_CHANGED) == []


def test_a_deactivation_names_the_admin_who_did_it(client, db_session, member):
    _register(client, email="admin@example.com")
    admin = _promote(db_session, "admin@example.com", Role.ADMIN)
    admin_tokens = _login(client, email="admin@example.com")

    response = client.patch(
        f"/api/v1/admin/users/{member.id}/status",
        json={"is_active": False},
        headers=_auth(admin_tokens),
    )

    assert response.status_code == 200
    entry = _trail(db_session, AuditAction.STATUS_CHANGED)[-1]
    assert entry.user_id == member.id
    assert entry.actor_user_id == admin.id
    assert entry.context == {"is_active": False}


def test_deleting_an_account_erases_its_trail(client, db_session, tokens, member):
    subject_id = member.id
    assert len(_trail(db_session)) > 0

    response = client.request(
        "DELETE",
        "/api/v1/users/me",
        json={"password": PASSWORD},
        headers=_auth(tokens),
    )

    assert response.status_code == 204
    assert (
        db_session.query(AuditLog).filter(AuditLog.user_id == subject_id).count() == 0
    )


def test_an_erasure_leaves_an_anonymous_tombstone(client, db_session, tokens):
    client.request(
        "DELETE",
        "/api/v1/users/me",
        json={"password": PASSWORD},
        headers=_auth(tokens),
    )

    # What survives is the fact that an erasure happened, which is what an
    # operator needs to reconcile a vanished account against.
    tombstones = _trail(db_session, AuditAction.ACCOUNT_DELETED)
    assert len(tombstones) == 1
    assert tombstones[0].user_id is None


def test_deleting_an_admin_disowns_their_actions_without_erasing_them(
    client, db_session, member
):
    _register(client, email="departing-admin@example.com")
    admin_id = _promote(db_session, "departing-admin@example.com", Role.ADMIN).id
    admin_tokens = _login(client, email="departing-admin@example.com")
    client.patch(
        f"/api/v1/admin/users/{member.id}/status",
        json={"is_active": False},
        headers=_auth(admin_tokens),
    )

    client.request(
        "DELETE",
        "/api/v1/users/me",
        json={"password": PASSWORD},
        headers=_auth(admin_tokens),
    )

    # The deletion happened in the request's own session, so drop what
    # this one has cached before reading the result back.
    db_session.expire_all()

    # The action stays, the attribution goes: deleting an admin must not
    # erase the record of what they did to other people's accounts.
    entry = _trail(db_session, AuditAction.STATUS_CHANGED)[-1]
    assert entry.user_id == member.id
    assert entry.actor_user_id is None
    assert db_session.get(User, admin_id) is None


def test_the_export_shape_stays_in_step_with_the_schema(client, tokens):
    export = client.get("/api/v1/users/me/export", headers=_auth(tokens)).json()

    # account_service says to keep this in step or the export under-reports.
    assert set(export) >= {
        "account",
        "subscription",
        "pending_email_tokens",
        "api_keys",
        "audit_log",
        "usage_events",
    }
    # Credentials are excluded from every section, trail included.
    assert not re.search(r"token_hash|hashed_password", str(export))

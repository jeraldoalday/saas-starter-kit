"""The HTTP surface of the audit trail.

The service tests cover what gets recorded and how it is stored. This file
covers the parts that only exist at the edge: who is allowed to read the
trail, what is safe to show each of them, and which credential is enough.
"""

import uuid

import pytest

from app.models.audit_log import AuditAction, AuditOutcome
from app.models.role import Role
from app.models.user import User

PASSWORD = "s3cret-password"
EMAIL = "audit-api@example.com"


def _register(client, email):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "full_name": "Trail Reader"},
    )
    assert response.status_code == 201


def _login(client, email):
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200
    return response.json()


def _auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _account(client, email=EMAIL, role: Role = Role.MEMBER, db_session=None):
    _register(client, email)
    if role is not Role.MEMBER:
        assert db_session is not None
        user = db_session.query(User).filter(User.email == email).one()
        user.role = role.value
        db_session.commit()
    return _login(client, email)


@pytest.fixture
def member(client):
    return _account(client)


def _fill_trail(client, member, pairs=2):
    """Record `pairs` * 2 rows without running into the key allowance.

    Each key is revoked as soon as it is minted, which both records a second
    row and frees the slot for the next one. Creating four keys outright
    would be simpler to read but would depend on the plan's ceiling, and the
    free tier's is lower than four — this is a test about paging, not about
    what a plan buys.
    """
    for _ in range(pairs):
        created = client.post(
            "/api/v1/api-keys", json={"name": "key"}, headers=_auth(member)
        ).json()
        client.delete(f"/api/v1/api-keys/{created['id']}", headers=_auth(member))


@pytest.fixture
def admin(client, db_session):
    return _account(
        client, email="admin-reader@example.com", role=Role.ADMIN, db_session=db_session
    )


def test_reading_a_trail_requires_authentication(client):
    assert client.get("/api/v1/audit/me").status_code == 401


def test_an_account_sees_its_own_history(client, member):
    body = client.get("/api/v1/audit/me", headers=_auth(member)).json()

    actions = [entry["action"] for entry in body["entries"]]
    assert AuditAction.LOGIN.value in actions
    assert AuditAction.ACCOUNT_REGISTERED.value in actions
    assert body["total"] == len(body["entries"])


def test_the_trail_is_newest_first(client, member):
    body = client.get("/api/v1/audit/me", headers=_auth(member)).json()

    stamps = [entry["created_at"] for entry in body["entries"]]
    assert stamps == sorted(stamps, reverse=True)


def test_an_account_never_sees_another_accounts_trail(client, member):
    _account(client, email="somebody-else@example.com")

    body = client.get("/api/v1/audit/me", headers=_auth(member)).json()

    # Two accounts registered and logged in; only one account's rows here.
    assert body["total"] == 2


def test_the_subject_view_never_names_the_actor(client, db_session, admin):
    subject_tokens = _account(client, email="acted-upon@example.com")
    subject = db_session.query(User).filter(User.email == "acted-upon@example.com").one()
    client.patch(
        f"/api/v1/admin/users/{subject.id}/status",
        json={"is_active": False},
        headers=_auth(admin),
    )
    # The PATCH ran in its own session, so this one still has the stale
    # value cached and assigning True would look like no change at all.
    db_session.refresh(subject)
    subject.is_active = True
    db_session.commit()

    body = client.get("/api/v1/audit/me", headers=_auth(subject_tokens)).json()
    changed = [
        entry
        for entry in body["entries"]
        if entry["action"] == AuditAction.STATUS_CHANGED.value
    ]

    # The subject learns that somebody else did it, not who: naming the
    # admin would hand out an identifier they have no claim to.
    assert len(changed) == 1
    assert changed[0]["by_another_account"] is True
    assert "actor_user_id" not in changed[0]


def test_an_api_key_cannot_read_the_trail(client, member):
    created = client.post(
        "/api/v1/api-keys", json={"name": "CI"}, headers=_auth(member)
    ).json()

    response = client.get("/api/v1/audit/me", headers={"X-API-Key": created["key"]})

    # A leaked key can read and write the product; the owner's security
    # history is a list of the addresses they sign in from and a map of when
    # they are not watching.
    assert response.status_code == 401


def test_pagination_reports_a_total_larger_than_the_page(client, member):
    # Registration and the sign-in that follows it are two rows; the helper
    # adds four more.
    _fill_trail(client, member)

    body = client.get("/api/v1/audit/me?limit=2", headers=_auth(member)).json()

    assert len(body["entries"]) == 2
    assert body["total"] == 6
    assert body["limit"] == 2


def test_paging_through_never_repeats_a_row(client, member):
    _fill_trail(client, member)

    first = client.get("/api/v1/audit/me?limit=3", headers=_auth(member)).json()
    second = client.get(
        "/api/v1/audit/me?limit=3&offset=3", headers=_auth(member)
    ).json()

    ids = [entry["id"] for entry in first["entries"] + second["entries"]]
    assert len(set(ids)) == 6


def test_an_oversized_page_is_refused_rather_than_silently_clamped(client, member):
    # A client asking for 10,000 rows has misunderstood something, and
    # quietly handing back 200 would let it keep misunderstanding.
    assert client.get(
        "/api/v1/audit/me?limit=10000", headers=_auth(member)
    ).status_code == 422


def test_a_member_cannot_read_the_deployment_wide_trail(client, member):
    assert client.get("/api/v1/audit", headers=_auth(member)).status_code == 403
    assert client.get("/api/v1/audit/summary", headers=_auth(member)).status_code == 403


def test_an_admin_reads_across_accounts(client, member, admin):
    body = client.get("/api/v1/audit", headers=_auth(admin)).json()

    subjects = {entry["user_id"] for entry in body["entries"]}
    assert len(subjects) == 2


def test_the_admin_view_names_both_parties(client, db_session, admin):
    _account(client, email="demoted@example.com")
    subject = db_session.query(User).filter(User.email == "demoted@example.com").one()
    admin_user = (
        db_session.query(User).filter(User.email == "admin-reader@example.com").one()
    )
    client.patch(
        f"/api/v1/admin/users/{subject.id}/status",
        json={"is_active": False},
        headers=_auth(admin),
    )

    body = client.get(
        f"/api/v1/audit?action={AuditAction.STATUS_CHANGED.value}",
        headers=_auth(admin),
    ).json()

    # Correlating one person's actions across accounts is what
    # reconstructing an incident consists of.
    assert body["total"] == 1
    assert body["entries"][0]["user_id"] == str(subject.id)
    assert body["entries"][0]["actor_user_id"] == str(admin_user.id)


def test_filtering_by_outcome_finds_the_refusals(client, member, admin):
    client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": "wrong-password"}
    )

    body = client.get(
        f"/api/v1/audit?outcome={AuditOutcome.FAILURE.value}", headers=_auth(admin)
    ).json()

    assert body["total"] == 1
    assert body["entries"][0]["action"] == AuditAction.LOGIN_FAILED.value


def test_filtering_by_account_scopes_the_total_too(client, db_session, member, admin):
    subject = db_session.query(User).filter(User.email == EMAIL).one()

    body = client.get(f"/api/v1/audit?user_id={subject.id}", headers=_auth(admin)).json()

    # The total is counted with the same filters as the page, so "showing 2
    # of 2" cannot describe a different set from the rows being shown.
    assert body["total"] == len(body["entries"]) == 2


def test_an_unknown_account_filter_returns_an_empty_page(client, admin):
    body = client.get(f"/api/v1/audit?user_id={uuid.uuid4()}", headers=_auth(admin)).json()

    assert body == {"entries": [], "total": 0, "limit": 50, "offset": 0}


def test_an_unrecognised_action_matches_nothing_rather_than_failing(client, admin):
    body = client.get("/api/v1/audit?action=nonsense.retired", headers=_auth(admin)).json()

    # An action retired in a later version should quietly return empty
    # rather than break a saved filter.
    assert body["total"] == 0


def test_several_actions_can_be_requested_at_once(client, member, admin):
    body = client.get(
        f"/api/v1/audit?action={AuditAction.LOGIN.value}"
        f"&action={AuditAction.ACCOUNT_REGISTERED.value}",
        headers=_auth(admin),
    ).json()

    assert body["total"] == 4


def test_the_summary_breaks_out_failed_logins(client, member, admin):
    for _ in range(3):
        client.post(
            "/api/v1/auth/login", json={"email": EMAIL, "password": "wrong-password"}
        )

    body = client.get("/api/v1/audit/summary", headers=_auth(admin)).json()

    # The one number that answers a question on its own: is somebody
    # spraying the login endpoint right now.
    assert body["failed_logins"] == 3
    assert body["entries_by_action"][AuditAction.LOGIN_FAILED.value] == 3
    assert body["window_days"] == 7


def test_the_summary_window_is_bounded(client, admin):
    assert client.get(
        "/api/v1/audit/summary?days=10000", headers=_auth(admin)
    ).status_code == 422

import pytest

from app.models.role import Role, rank, satisfies
from app.models.user import User

PASSWORD = "s3cret-password"


def _make_user(client, db_session, email: str, role: Role = Role.MEMBER) -> dict:
    client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    user = db_session.query(User).filter(User.email == email).one()
    user.role = role.value
    db_session.commit()

    login = client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_new_users_default_to_member(client, db_session):
    client.post(
        "/api/v1/auth/register",
        json={"email": "default@example.com", "password": PASSWORD},
    )

    user = db_session.query(User).filter(User.email == "default@example.com").one()
    assert user.role == Role.MEMBER.value


@pytest.mark.parametrize(
    ("actual", "required", "expected"),
    [
        (Role.OWNER.value, Role.ADMIN, True),
        (Role.ADMIN.value, Role.ADMIN, True),
        (Role.MEMBER.value, Role.ADMIN, False),
        (Role.ADMIN.value, Role.OWNER, False),
        (Role.MEMBER.value, Role.MEMBER, True),
    ],
)
def test_role_hierarchy(actual, required, expected):
    assert satisfies(actual, required) is expected


def test_unknown_role_is_denied():
    """A corrupt or future role value must not accidentally grant access."""
    assert rank("superuser") == -1
    assert satisfies("superuser", Role.MEMBER) is False


def test_member_cannot_list_users(client, db_session):
    headers = _make_user(client, db_session, "member@example.com", Role.MEMBER)

    response = client.get("/api/v1/admin/users", headers=headers)

    assert response.status_code == 403


def test_admin_can_list_users(client, db_session):
    headers = _make_user(client, db_session, "admin@example.com", Role.ADMIN)

    response = client.get("/api/v1/admin/users", headers=headers)

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_owner_can_list_users(client, db_session):
    headers = _make_user(client, db_session, "owner@example.com", Role.OWNER)

    assert client.get("/api/v1/admin/users", headers=headers).status_code == 200


def test_admin_cannot_change_roles(client, db_session):
    headers = _make_user(client, db_session, "admin2@example.com", Role.ADMIN)
    target = db_session.query(User).filter(User.email == "admin2@example.com").one()

    response = client.patch(
        f"/api/v1/admin/users/{target.id}/role",
        json={"role": "owner"},
        headers=headers,
    )

    assert response.status_code == 403


def test_owner_can_promote_a_member(client, db_session):
    headers = _make_user(client, db_session, "owner2@example.com", Role.OWNER)
    client.post(
        "/api/v1/auth/register",
        json={"email": "promote@example.com", "password": PASSWORD},
    )
    target = db_session.query(User).filter(User.email == "promote@example.com").one()

    response = client.patch(
        f"/api/v1/admin/users/{target.id}/role",
        json={"role": "admin"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["role"] == "admin"


def test_owner_cannot_demote_themselves(client, db_session):
    headers = _make_user(client, db_session, "owner3@example.com", Role.OWNER)
    owner = db_session.query(User).filter(User.email == "owner3@example.com").one()

    response = client.patch(
        f"/api/v1/admin/users/{owner.id}/role",
        json={"role": "member"},
        headers=headers,
    )

    assert response.status_code == 400


def test_unknown_user_returns_404(client, db_session):
    headers = _make_user(client, db_session, "owner4@example.com", Role.OWNER)

    response = client.patch(
        "/api/v1/admin/users/00000000-0000-0000-0000-000000000000/role",
        json={"role": "admin"},
        headers=headers,
    )

    assert response.status_code == 404


def test_invalid_role_value_is_rejected(client, db_session):
    headers = _make_user(client, db_session, "owner5@example.com", Role.OWNER)
    owner = db_session.query(User).filter(User.email == "owner5@example.com").one()

    response = client.patch(
        f"/api/v1/admin/users/{owner.id}/role",
        json={"role": "superuser"},
        headers=headers,
    )

    assert response.status_code == 422


def test_admin_endpoints_require_authentication(client):
    assert client.get("/api/v1/admin/users").status_code == 401

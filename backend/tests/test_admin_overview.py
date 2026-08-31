"""The deployment-wide counts endpoint.

Worth testing separately from the rest of the admin surface because it is
the one endpoint whose cost scales with the whole deployment rather than
with one account, and because a count that is quietly wrong looks exactly
like one that is right.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.models.role import Role
from app.models.subscription import Subscription, SubscriptionStatus
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


@pytest.fixture
def admin(client, db_session) -> dict:
    return _make_user(client, db_session, "overview-admin@example.com", Role.ADMIN)


def _overview(client, headers) -> dict:
    response = client.get("/api/v1/admin/overview", headers=headers)
    assert response.status_code == 200
    return response.json()


def test_requires_authentication(client):
    assert client.get("/api/v1/admin/overview").status_code == 401


def test_members_are_refused(client, db_session):
    headers = _make_user(client, db_session, "nosy@example.com")

    assert client.get("/api/v1/admin/overview", headers=headers).status_code == 403


def test_counts_accounts_by_state(client, db_session, admin):
    _make_user(client, db_session, "second@example.com")
    inactive = db_session.query(User).filter(User.email == "second@example.com").one()
    inactive.is_active = False
    db_session.commit()

    body = _overview(client, admin)

    assert body["total_users"] == 2
    assert body["active_users"] == 1
    # Verification is off by default in tests, so nobody is verified yet.
    assert body["verified_users"] == 0


def test_counts_sessions_and_distinguishes_live_ones(client, db_session, admin):
    # The admin fixture logged in once; a second login is a second session.
    client.post(
        "/api/v1/auth/login",
        json={"email": "overview-admin@example.com", "password": PASSWORD},
    )

    before = _overview(client, admin)
    assert before["total_sessions"] == 2
    assert before["active_sessions"] == 2

    from app.models.auth_session import AuthSession

    stale = db_session.query(AuthSession).first()
    stale.revoked_at = datetime.now(UTC)
    db_session.commit()

    after = _overview(client, admin)
    # The gap between total and active is what says how much the retention
    # prune has left to do.
    assert after["total_sessions"] == 2
    assert after["active_sessions"] == 1


def test_counts_api_keys(client, admin):
    client.post(
        "/api/v1/api-keys", headers=admin, json={"name": "CI", "expires_in_days": None}
    )

    assert _overview(client, admin)["total_api_keys"] == 1


def test_counts_webhook_endpoints_and_the_queue_behind_them(
    client, db_session, admin
):
    """The pending figure is the operational one.

    Nothing is POSTed until the delivery worker runs, so a queue that keeps
    growing is the symptom of a worker that is not scheduled or has
    crashed — a failure that is otherwise completely silent.
    """
    from app.models.webhook_endpoint import WebhookEndpoint, WebhookEventType
    from app.services.webhooks.dispatch import queue_delivery

    user = db_session.query(User).filter(User.email == "overview-admin@example.com").one()
    endpoint = WebhookEndpoint(
        user_id=user.id,
        url="https://receiver.example.com/hooks",
        secret="whsec_test-secret",
        event_types=[],
        created_at=datetime.now(UTC),
    )
    db_session.add(endpoint)
    db_session.commit()

    assert _overview(client, admin)["webhook_endpoints"] == 1
    assert _overview(client, admin)["pending_webhook_deliveries"] == 0

    queue_delivery(db_session, endpoint, WebhookEventType.PING, {})

    assert _overview(client, admin)["pending_webhook_deliveries"] == 1


def test_groups_subscriptions_by_status(client, db_session, admin):
    user = db_session.query(User).filter(User.email == "overview-admin@example.com").one()
    db_session.add(
        Subscription(
            user_id=user.id,
            status=SubscriptionStatus.ACTIVE.value,
            current_period_end=datetime.now(UTC) + timedelta(days=30),
        )
    )
    db_session.commit()

    body = _overview(client, admin)

    assert body["subscriptions_by_status"] == {"active": 1}


def test_absent_statuses_are_omitted_rather_than_zeroed(client, admin):
    # A status added to the enum later needs no change to this endpoint.
    assert _overview(client, admin)["subscriptions_by_status"] == {}


class TestSecurityFigures:
    """The security half of "is anything obviously wrong right now".

    Both figures are here rather than on the audit endpoints because an
    operator checking on a deployment should not have to know that a
    separate route exists before they can see somebody spraying the login
    endpoint.
    """

    def test_a_quiet_deployment_reports_zero(self, client, admin):
        body = _overview(client, admin)

        assert body["failed_logins_last_hour"] == 0

    def test_refused_sign_ins_are_counted(self, client, db_session, admin):
        client.post(
            "/api/v1/auth/register",
            json={"email": "target@example.com", "password": PASSWORD},
        )
        for _ in range(3):
            client.post(
                "/api/v1/auth/login",
                json={"email": "target@example.com", "password": "wrong-password"},
            )

        assert _overview(client, admin)["failed_logins_last_hour"] == 3

    def test_attempts_against_unknown_addresses_count_too(self, client, admin):
        client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "wrong-password"},
        )

        # A spray is mostly misses; counting only the hits would hide it.
        assert _overview(client, admin)["failed_logins_last_hour"] == 1

    def test_older_refusals_fall_out_of_the_window(self, client, db_session, admin):
        from app.models.audit_log import AuditAction, AuditLog

        db_session.add(
            AuditLog(
                action=AuditAction.LOGIN_FAILED.value,
                outcome="failure",
                context={},
                created_at=datetime.now(UTC) - timedelta(hours=3),
            )
        )
        db_session.commit()

        body = _overview(client, admin)

        # A total that only ever goes up answers nothing; the question is
        # whether somebody is trying accounts now.
        assert body["failed_logins_last_hour"] == 0
        assert body["audit_entries"] > 0

    def test_the_trail_size_is_reported_for_the_prune(self, client, admin):
        before = _overview(client, admin)["audit_entries"]

        client.post(
            "/api/v1/auth/register",
            json={"email": "counted@example.com", "password": PASSWORD},
        )

        # A figure far above what the retention window should hold is how an
        # operator finds out the scheduled prune stopped running.
        assert _overview(client, admin)["audit_entries"] > before

    def test_a_member_still_cannot_read_it(self, client, db_session):
        member = _make_user(client, db_session, "not-an-admin@example.com")

        assert client.get("/api/v1/admin/overview", headers=member).status_code == 403

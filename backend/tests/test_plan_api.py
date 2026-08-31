"""The HTTP surface of plans, quotas and the ceilings they enforce.

tests/test_entitlements.py covers the catalog and the arithmetic. This file
covers what a client actually sees: which credential may read a plan, what
a refused write says, and whether a partly-spent allowance is reported
honestly enough for a client to back off on its own.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.entitlements import FREE_PLAN, PRO_PLAN
from app.models.audit_log import AuditAction, AuditLog
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.usage_event import UsageEvent
from app.models.user import User
from app.services.entitlement_service import QUOTA_KEYS, month_start

EMAIL = "plan-api@example.com"
PASSWORD = "s3cret-password"


def _session(client, email=EMAIL):
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "full_name": "Plan Reader"},
    )
    login = client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _user(db_session, email=EMAIL) -> User:
    return db_session.query(User).filter(User.email == email).one()


def _subscribe(db_session, email=EMAIL, status=SubscriptionStatus.ACTIVE):
    user = _user(db_session, email)
    db_session.add(Subscription(user_id=user.id, status=status.value))
    db_session.commit()
    return user


def _quotas(body) -> dict[str, dict]:
    return {quota["key"]: quota for quota in body["quotas"]}


# -------------------------------------------------------------- /plan


def test_reading_a_plan_requires_authentication(client):
    assert client.get("/api/v1/plan").status_code == 401


def test_a_new_account_is_reported_on_the_free_plan(client):
    headers = _session(client)

    body = client.get("/api/v1/plan", headers=headers).json()

    assert body["entitlements"]["plan_key"] == FREE_PLAN.plan_key
    assert body["entitlements"]["display_name"] == FREE_PLAN.display_name
    # Never subscribed, which is a different story from a lapsed card and
    # is reported as such.
    assert body["subscription_status"] is None


def test_a_subscribed_account_is_reported_on_the_paid_plan(client, db_session):
    headers = _session(client)
    _subscribe(db_session)

    body = client.get("/api/v1/plan", headers=headers).json()

    assert body["entitlements"]["plan_key"] == PRO_PLAN.plan_key
    assert body["subscription_status"] == SubscriptionStatus.ACTIVE.value
    assert _quotas(body)["api_keys"]["limit"] == PRO_PLAN.max_active_api_keys


def test_a_past_due_account_reads_free_but_says_why(client, db_session):
    headers = _session(client)
    _subscribe(db_session, status=SubscriptionStatus.PAST_DUE)

    body = client.get("/api/v1/plan", headers=headers).json()

    assert body["entitlements"]["plan_key"] == FREE_PLAN.plan_key
    assert body["subscription_status"] == SubscriptionStatus.PAST_DUE.value


def test_the_plan_reports_every_quota_with_what_is_left(client):
    headers = _session(client)

    body = client.get("/api/v1/plan", headers=headers).json()

    quotas = _quotas(body)
    assert set(quotas) == set(QUOTA_KEYS)
    for quota in quotas.values():
        assert quota["remaining"] == quota["limit"] - quota["used"]
        assert quota["exhausted"] is False
    # One session: the login that produced these headers.
    assert quotas["sessions"]["used"] == 1


def test_the_period_runs_from_this_month_to_the_next(client):
    headers = _session(client)

    body = client.get("/api/v1/plan", headers=headers).json()

    start = datetime.fromisoformat(body["period_start"])
    end = datetime.fromisoformat(body["period_end"])
    assert start == month_start()
    assert end > start
    assert end.day == 1


def test_creating_a_key_moves_the_quota(client):
    headers = _session(client)
    before = _quotas(client.get("/api/v1/plan", headers=headers).json())

    client.post("/api/v1/api-keys", json={"name": "CI"}, headers=headers)

    after = _quotas(client.get("/api/v1/plan", headers=headers).json())
    assert after["api_keys"]["used"] == before["api_keys"]["used"] + 1
    assert after["api_keys"]["remaining"] == before["api_keys"]["remaining"] - 1


def test_a_spent_quota_reports_itself_exhausted(client):
    headers = _session(client)
    for index in range(FREE_PLAN.max_active_api_keys):
        client.post("/api/v1/api-keys", json={"name": f"Key {index}"}, headers=headers)

    quota = _quotas(client.get("/api/v1/plan", headers=headers).json())["api_keys"]

    assert quota["remaining"] == 0
    assert quota["exhausted"] is True


def test_an_api_key_may_read_its_own_plan(client):
    headers = _session(client)
    raw_key = client.post(
        "/api/v1/api-keys", json={"name": "CI"}, headers=headers
    ).json()["key"]

    response = client.get("/api/v1/plan", headers={"X-API-Key": raw_key})

    # Unlike the credential and audit routes: a programmatic client's own
    # remaining allowance is exactly what it needs in order to back off
    # before it starts being refused.
    assert response.status_code == 200
    assert response.json()["entitlements"]["plan_key"] == FREE_PLAN.plan_key


# ------------------------------------------------------------ /plan/catalog


def test_the_catalog_lists_free_first_then_priced_plans(client):
    headers = _session(client)

    entries = client.get("/api/v1/plan/catalog", headers=headers).json()

    assert entries[0]["entitlements"]["plan_key"] == FREE_PLAN.plan_key
    # Free is not a Stripe product, so null rather than a zero that would
    # imply a line item nobody is charged for.
    assert entries[0]["price"] is None
    assert entries[0]["is_current"] is True

    paid = entries[1]
    assert paid["entitlements"]["plan_key"] == PRO_PLAN.plan_key
    assert paid["price"]["unit_amount"] > 0
    assert paid["price"]["currency"]
    assert paid["is_current"] is False


def test_the_catalog_marks_the_subscriber_plan_as_current(client, db_session):
    headers = _session(client)
    _subscribe(db_session)

    entries = client.get("/api/v1/plan/catalog", headers=headers).json()

    current = [entry for entry in entries if entry["is_current"]]
    assert [entry["entitlements"]["plan_key"] for entry in current] == [
        PRO_PLAN.plan_key
    ]


def test_the_catalog_is_ordered_cheapest_first(client):
    headers = _session(client)

    entries = client.get("/api/v1/plan/catalog", headers=headers).json()

    prices = [entry["price"]["unit_amount"] if entry["price"] else 0 for entry in entries]
    assert prices == sorted(prices)


# ----------------------------------------------------------- enforcement


def test_a_refused_key_names_the_plan_and_the_ceiling(client):
    headers = _session(client)
    for index in range(FREE_PLAN.max_active_api_keys):
        client.post("/api/v1/api-keys", json={"name": f"Key {index}"}, headers=headers)

    response = client.post("/api/v1/api-keys", json={"name": "One more"}, headers=headers)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert FREE_PLAN.display_name in detail
    # Actionable: says what to do rather than only what went wrong.
    assert "upgrade" in detail.lower()


def _spend_the_event_allowance(db_session, user, leaving=0):
    """Fill this month's allowance, leaving room for `leaving` more events."""
    spent = (
        db_session.query(UsageEvent)
        .filter(UsageEvent.user_id == user.id)
        .filter(UsageEvent.occurred_at >= month_start())
        .count()
    )
    to_add = FREE_PLAN.monthly_event_allowance - spent - leaving
    db_session.add_all(
        [UsageEvent(user_id=user.id, name="filler.event") for _ in range(to_add)]
    )
    db_session.commit()


def test_ingest_is_refused_once_the_monthly_allowance_is_spent(client, db_session):
    headers = _session(client)
    _spend_the_event_allowance(db_session, _user(db_session))

    response = client.post(
        "/api/v1/analytics/events",
        json={"events": [{"name": "dashboard.viewed"}]},
        headers=headers,
    )

    assert response.status_code == 429
    assert FREE_PLAN.display_name in response.json()["detail"]
    # Pointing at the reset, which is a long way off — and is the honest
    # answer, since retrying sooner cannot succeed.
    assert int(response.headers["Retry-After"]) > 0


def test_an_overshooting_batch_is_clipped_rather_than_refused(client, db_session):
    headers = _session(client)
    _spend_the_event_allowance(db_session, _user(db_session), leaving=2)

    response = client.post(
        "/api/v1/analytics/events",
        json={"events": [{"name": f"a.event_{index}"} for index in range(5)]},
        headers=headers,
    )

    assert response.status_code == 202
    body = response.json()
    # Two taken, three dropped — and the client is told, which is the whole
    # reason `dropped` exists.
    assert body["recorded"] == 2
    assert body["dropped"] == 3
    assert body["remaining"] == 0


def test_ingest_reports_what_is_left_of_the_allowance(client):
    headers = _session(client)

    body = client.post(
        "/api/v1/analytics/events",
        json={"events": [{"name": "dashboard.viewed"}]},
        headers=headers,
    ).json()

    assert body["dropped"] == 0
    assert 0 < body["remaining"] < FREE_PLAN.monthly_event_allowance


# --------------------------------------------------------- audit window


def _backdate_trail(db_session, user, days_ago):
    db_session.add(
        AuditLog(
            user_id=user.id,
            action=AuditAction.LOGIN.value,
            created_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
    )
    db_session.commit()


def test_the_free_plan_reads_a_bounded_window_of_its_own_trail(client, db_session):
    headers = _session(client)
    user = _user(db_session)
    _backdate_trail(db_session, user, FREE_PLAN.audit_history_days + 5)

    body = client.get("/api/v1/audit/me", headers=headers).json()

    assert body["history_days"] == FREE_PLAN.audit_history_days
    # The backdated row is outside the window, so neither the page nor the
    # total may include it — a page counted against the whole table is how a
    # listing promises a page that returns nothing.
    created = [datetime.fromisoformat(entry["created_at"]) for entry in body["entries"]]
    cutoff = datetime.now(UTC) - timedelta(days=FREE_PLAN.audit_history_days)
    assert all(entry.replace(tzinfo=UTC) >= cutoff for entry in created)
    assert body["total"] == len(body["entries"])


def test_upgrading_reveals_history_that_was_already_recorded(client, db_session):
    headers = _session(client)
    user = _user(db_session)
    _backdate_trail(db_session, user, FREE_PLAN.audit_history_days + 5)
    assert client.get("/api/v1/audit/me", headers=headers).json()["total"] == 2

    _subscribe(db_session)

    body = client.get("/api/v1/audit/me", headers=headers).json()
    # The row was never deleted, only hidden — upgrading reveals it rather
    # than starting a clock.
    assert body["history_days"] == PRO_PLAN.audit_history_days
    assert body["total"] == 3


@pytest.mark.parametrize("path", ["/api/v1/plan", "/api/v1/plan/catalog"])
def test_plan_routes_reject_a_bad_token(client, path):
    assert client.get(path, headers={"Authorization": "Bearer nope"}).status_code == 401

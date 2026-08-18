import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import get_settings
from app.models.role import Role
from app.models.usage_event import EventName, EventSource, UsageEvent
from app.models.user import User
from app.services import analytics_service
from app.services.analytics_service import (
    MAX_WINDOW_DAYS,
    clamp_window,
    count_active_users,
    count_events,
    counts_by_name,
    daily_counts,
    prune_events,
    recent_events,
    record_event,
    record_events,
    summarize_usage,
    summarize_user_usage,
)

PASSWORD = "s3cret-password"


def _register(client, email: str) -> None:
    client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})


def _auth_headers(client, db_session, email: str, role: Role = Role.MEMBER) -> dict:
    _register(client, email)
    user = db_session.query(User).filter(User.email == email).one()
    user.role = role.value
    db_session.commit()

    login = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _make_user(db_session, email: str = "u@example.com") -> User:
    user = User(email=email, hashed_password="not-a-real-hash")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


# --- recording ------------------------------------------------------------


def test_record_event_stores_the_row(db_session):
    user = _make_user(db_session)

    event = record_event(
        db_session, EventName.USER_LOGGED_IN, user=user, properties={"via": "password"}
    )

    assert event is not None
    stored = db_session.query(UsageEvent).one()
    assert stored.name == "user.logged_in"
    assert stored.user_id == user.id
    assert stored.source == EventSource.BACKEND.value
    assert stored.properties == {"via": "password"}


def test_record_event_accepts_a_bare_user_id(db_session):
    """Webhook handlers hold a subscription's user_id, not the user row."""
    user = _make_user(db_session)

    record_event(db_session, EventName.SUBSCRIPTION_ACTIVATED, user_id=user.id)

    assert db_session.query(UsageEvent).one().user_id == user.id


def test_events_can_be_anonymous(db_session):
    record_event(db_session, "landing.viewed")

    assert db_session.query(UsageEvent).one().user_id is None


def test_recording_is_skipped_when_analytics_is_disabled(db_session, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "analytics_enabled", False)

    assert record_event(db_session, "landing.viewed") is None
    assert db_session.query(UsageEvent).count() == 0


def test_recording_failures_do_not_propagate(db_session, monkeypatch):
    """A broken analytics insert must not fail the request that caused it."""

    def _explode(*_args, **_kwargs):
        from sqlalchemy.exc import SQLAlchemyError

        raise SQLAlchemyError("database is on fire")

    monkeypatch.setattr(db_session, "add", _explode)

    assert record_event(db_session, "landing.viewed") is None


def test_record_events_batches(db_session):
    user = _make_user(db_session)

    stored = record_events(
        db_session,
        [("dashboard.viewed", {}), ("billing.card_clicked", {"plan": "pro"})],
        user=user,
    )

    assert stored == 2
    assert {event.source for event in db_session.query(UsageEvent).all()} == {
        EventSource.FRONTEND.value
    }


# --- aggregation ----------------------------------------------------------


def _seed(db_session, user, *offsets_in_days: int, name: str = "dashboard.viewed") -> None:
    now = datetime.now(UTC)
    for offset in offsets_in_days:
        record_event(
            db_session, name, user=user, occurred_at=now - timedelta(days=offset)
        )


def test_count_events_filters_by_window_and_name(db_session):
    user = _make_user(db_session)
    _seed(db_session, user, 0, 1, 30)
    _seed(db_session, user, 0, name="billing.checkout_started")

    since = datetime.now(UTC) - timedelta(days=7)
    assert count_events(db_session) == 4
    assert count_events(db_session, since=since) == 3
    assert count_events(db_session, since=since, name="dashboard.viewed") == 2


def test_count_active_users_ignores_anonymous_events(db_session):
    first = _make_user(db_session, "first@example.com")
    second = _make_user(db_session, "second@example.com")
    _seed(db_session, first, 0, 0)
    _seed(db_session, second, 0)
    record_event(db_session, "landing.viewed")

    since = datetime.now(UTC) - timedelta(days=1)
    assert count_active_users(db_session, since=since) == 2


def test_counts_by_name_orders_by_frequency(db_session):
    user = _make_user(db_session)
    _seed(db_session, user, 0, 0, 0)
    _seed(db_session, user, 0, name="billing.checkout_started")

    counts = counts_by_name(db_session, since=datetime.now(UTC) - timedelta(days=1))

    assert list(counts) == ["dashboard.viewed", "billing.checkout_started"]
    assert counts == {"dashboard.viewed": 3, "billing.checkout_started": 1}


def test_daily_counts_fills_quiet_days_with_zero(db_session):
    user = _make_user(db_session)
    _seed(db_session, user, 0, 2)

    series = daily_counts(db_session, days=4)

    assert [entry.count for entry in series] == [0, 1, 0, 1]
    assert [entry.day for entry in series] == sorted(entry.day for entry in series)


def test_daily_counts_window_is_whole_days(db_session):
    """A 1-day window is today, not the last 24 hours."""
    user = _make_user(db_session)
    _seed(db_session, user, 0)

    series = daily_counts(db_session, days=1)

    assert len(series) == 1
    assert series[0].day == datetime.now(UTC).date()
    assert series[0].count == 1


def test_daily_counts_excludes_events_outside_the_window(db_session):
    user = _make_user(db_session)
    _seed(db_session, user, 10)

    assert sum(entry.count for entry in daily_counts(db_session, days=3)) == 0


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(0, 1), (-5, 1), (14, 14), (MAX_WINDOW_DAYS + 100, MAX_WINDOW_DAYS)],
)
def test_clamp_window(requested, expected):
    assert clamp_window(requested) == expected


def test_summarize_usage_reports_the_whole_account(db_session):
    first = _make_user(db_session, "first@example.com")
    second = _make_user(db_session, "second@example.com")
    _seed(db_session, first, 0, 1)
    _seed(db_session, second, 0, name="billing.checkout_started")

    summary = summarize_usage(db_session, days=7)

    assert summary.window_days == 7
    assert summary.total_events == 3
    assert summary.active_users == 2
    assert summary.events_by_name["dashboard.viewed"] == 2
    assert len(summary.daily) == 7


def test_summarize_user_usage_is_scoped_to_one_account(db_session):
    mine = _make_user(db_session, "mine@example.com")
    theirs = _make_user(db_session, "theirs@example.com")
    _seed(db_session, mine, 0)
    _seed(db_session, theirs, 0, 0, 0)

    summary = summarize_user_usage(db_session, mine, days=7)

    assert summary.total_events == 1
    assert summary.active_users == 1
    assert summary.events_by_name == {"dashboard.viewed": 1}


def test_summarize_user_usage_reports_no_active_user_when_silent(db_session):
    user = _make_user(db_session)

    assert summarize_user_usage(db_session, user, days=7).active_users == 0


def test_recent_events_is_newest_first(db_session):
    user = _make_user(db_session)
    _seed(db_session, user, 5, name="older.event")
    _seed(db_session, user, 0, name="newer.event")

    assert [event.name for event in recent_events(db_session)] == [
        "newer.event",
        "older.event",
    ]


def test_prune_events_removes_only_old_rows(db_session):
    user = _make_user(db_session)
    _seed(db_session, user, 0, 5, 100)

    deleted = prune_events(db_session, older_than_days=30)

    assert deleted == 1
    assert db_session.query(UsageEvent).count() == 2


# --- ingestion endpoint ---------------------------------------------------


def test_ingest_records_events_for_the_caller(client, db_session):
    headers = _auth_headers(client, db_session, "ingest@example.com")

    response = client.post(
        "/api/v1/analytics/events",
        json={"events": [{"name": "dashboard.viewed", "properties": {"tab": "billing"}}]},
        headers=headers,
    )

    assert response.status_code == 202
    assert response.json() == {"recorded": 1}
    stored = db_session.query(UsageEvent).filter(UsageEvent.name == "dashboard.viewed").one()
    assert stored.source == EventSource.FRONTEND.value


def test_ingest_requires_authentication(client):
    response = client.post(
        "/api/v1/analytics/events", json={"events": [{"name": "dashboard.viewed"}]}
    )

    assert response.status_code == 401


@pytest.mark.parametrize(
    "name",
    ["Dashboard Viewed", "dashboard viewed", "", "9lives", "dashboard..viewed", "x" * 65],
)
def test_ingest_rejects_malformed_event_names(client, db_session, name):
    headers = _auth_headers(client, db_session, f"name-{abs(hash(name))}@example.com")

    response = client.post(
        "/api/v1/analytics/events", json={"events": [{"name": name}]}, headers=headers
    )

    assert response.status_code == 422


def test_ingest_rejects_oversized_property_bags(client, db_session):
    headers = _auth_headers(client, db_session, "props@example.com")

    response = client.post(
        "/api/v1/analytics/events",
        json={
            "events": [
                {"name": "dashboard.viewed", "properties": {f"k{i}": i for i in range(20)}}
            ]
        },
        headers=headers,
    )

    assert response.status_code == 422


def test_ingest_rejects_batches_over_the_configured_limit(client, db_session, monkeypatch):
    headers = _auth_headers(client, db_session, "batch@example.com")
    monkeypatch.setattr(get_settings(), "analytics_max_batch_size", 2)

    response = client.post(
        "/api/v1/analytics/events",
        json={"events": [{"name": "dashboard.viewed"} for _ in range(3)]},
        headers=headers,
    )

    assert response.status_code == 413


def test_ingest_rejects_an_empty_batch(client, db_session):
    headers = _auth_headers(client, db_session, "empty@example.com")

    response = client.post(
        "/api/v1/analytics/events", json={"events": []}, headers=headers
    )

    assert response.status_code == 422


# --- summary endpoints ----------------------------------------------------


def test_my_usage_covers_only_the_caller(client, db_session):
    mine = _auth_headers(client, db_session, "mine@example.com")
    _auth_headers(client, db_session, "theirs@example.com")

    response = client.get("/api/v1/analytics/me?days=7", headers=mine)

    assert response.status_code == 200
    body = response.json()
    # Registering and logging in are recorded server-side, once each.
    assert body["events_by_name"] == {"user.logged_in": 1, "user.registered": 1}
    assert body["total_events"] == 2
    assert len(body["daily"]) == 7


def test_my_usage_rejects_an_out_of_range_window(client, db_session):
    headers = _auth_headers(client, db_session, "window@example.com")

    assert client.get("/api/v1/analytics/me?days=0", headers=headers).status_code == 422
    assert (
        client.get(
            f"/api/v1/analytics/me?days={MAX_WINDOW_DAYS + 1}", headers=headers
        ).status_code
        == 422
    )


def test_summary_is_admin_only(client, db_session):
    member = _auth_headers(client, db_session, "member@example.com")

    assert client.get("/api/v1/analytics/summary", headers=member).status_code == 403


def test_summary_aggregates_every_account_for_an_admin(client, db_session):
    admin = _auth_headers(client, db_session, "admin@example.com", Role.ADMIN)
    _auth_headers(client, db_session, "other@example.com")

    body = client.get("/api/v1/analytics/summary?days=7", headers=admin).json()

    assert body["active_users"] == 2
    assert body["total_events"] == 4
    assert body["events_by_name"]["user.registered"] == 2


def test_event_feed_can_be_filtered_by_user(client, db_session):
    admin = _auth_headers(client, db_session, "admin@example.com", Role.ADMIN)
    _auth_headers(client, db_session, "target@example.com")
    target = db_session.query(User).filter(User.email == "target@example.com").one()

    body = client.get(
        f"/api/v1/analytics/events?user_id={target.id}", headers=admin
    ).json()

    assert {entry["name"] for entry in body} == {"user.registered", "user.logged_in"}
    assert all(entry["user_id"] == str(target.id) for entry in body)


def test_event_feed_is_admin_only(client, db_session):
    member = _auth_headers(client, db_session, "member@example.com")

    assert client.get("/api/v1/analytics/events", headers=member).status_code == 403


def test_event_feed_tolerates_an_unknown_user_filter(client, db_session):
    admin = _auth_headers(client, db_session, "admin@example.com", Role.ADMIN)

    response = client.get(
        f"/api/v1/analytics/events?user_id={uuid.uuid4()}", headers=admin
    )

    assert response.status_code == 200
    assert response.json() == []


# --- lifecycle events -----------------------------------------------------


def test_registration_and_login_are_recorded(client, db_session):
    _auth_headers(client, db_session, "lifecycle@example.com")

    names = [event.name for event in db_session.query(UsageEvent).all()]
    assert names.count(EventName.USER_REGISTERED.value) == 1
    assert names.count(EventName.USER_LOGGED_IN.value) == 1


def test_failed_logins_are_not_recorded(client, db_session):
    _register(client, "wrongpass@example.com")

    client.post(
        "/api/v1/auth/login",
        json={"email": "wrongpass@example.com", "password": "not-the-password"},
    )

    assert (
        db_session.query(UsageEvent)
        .filter(UsageEvent.name == EventName.USER_LOGGED_IN.value)
        .count()
        == 0
    )


def test_email_verification_is_recorded(client, db_session, outbox):
    _register(client, "verify@example.com")
    token = outbox.outbox[-1].text_body.split("token=")[1].split()[0]

    client.post("/api/v1/auth/verify-email", json={"token": token})

    assert (
        db_session.query(UsageEvent)
        .filter(UsageEvent.name == EventName.USER_VERIFIED.value)
        .count()
        == 1
    )


def test_account_deletion_is_recorded_anonymously(client, db_session):
    headers = _auth_headers(client, db_session, "gone@example.com")

    response = client.request(
        "DELETE", "/api/v1/users/me", json={"password": PASSWORD}, headers=headers
    )

    assert response.status_code == 204
    deletion = (
        db_session.query(UsageEvent)
        .filter(UsageEvent.name == EventName.ACCOUNT_DELETED.value)
        .one()
    )
    assert deletion.user_id is None


def test_deleting_an_account_erases_its_event_history(client, db_session):
    headers = _auth_headers(client, db_session, "erase@example.com")
    user_id = db_session.query(User).filter(User.email == "erase@example.com").one().id

    client.request(
        "DELETE", "/api/v1/users/me", json={"password": PASSWORD}, headers=headers
    )

    assert (
        db_session.query(UsageEvent).filter(UsageEvent.user_id == user_id).count() == 0
    )


def test_service_module_exposes_a_stable_surface():
    """The prune script and endpoints import these by name."""
    for exported in analytics_service.__all__:
        assert hasattr(analytics_service, exported)

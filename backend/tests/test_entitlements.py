"""The plan catalog and the arithmetic that reads it.

Two halves. The first asserts properties of the catalog itself — that every
plan names every limit, that no plan promises more than the services will
actually give — because those are the failures a plan added later
introduces, and none of them show up as an exception. The second covers
resolution and counting at the service level, where a wrong answer is a
customer either refused something they paid for or handed something they
did not.
"""

import uuid
from dataclasses import fields
from datetime import UTC, datetime, timedelta

import pytest

from app.core.billing_plans import BILLING_PLANS
from app.core.entitlements import (
    ENTITLEMENTS_BY_PLAN,
    FREE_PLAN,
    FREE_PLAN_KEY,
    LIMIT_FIELDS,
    PRO_PLAN,
    Entitlements,
    entitlements_for_plan,
    is_exhausted,
    limit_for,
    remaining,
)
from app.core.security import create_refresh_token, hash_password
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.usage_event import UsageEvent
from app.models.user import User
from app.services.api_key_service import (
    MAX_ACTIVE_KEYS_PER_USER,
    create_api_key,
    effective_key_limit,
)
from app.services.entitlement_service import (
    PAID_PLAN_KEY,
    QUOTA_KEYS,
    Quota,
    QuotaExceededError,
    allowance_for_events,
    api_key_quota,
    check_api_key_quota,
    entitlements_for,
    event_quota,
    month_start,
    next_period_start,
    plan_key_for,
    quota_snapshot,
    session_quota,
)
from app.services.session_service import (
    MAX_ACTIVE_SESSIONS_PER_USER,
    count_active_sessions,
    effective_session_cap,
    start_session,
)
from app.services.webhooks.endpoints import MAX_ENDPOINTS_PER_USER

PASSWORD = "s3cret-password"


@pytest.fixture
def user(db_session) -> User:
    account = User(
        email="entitled@example.com",
        hashed_password=hash_password(PASSWORD),
        full_name="Plan Holder",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _subscribe(db_session, user, status=SubscriptionStatus.ACTIVE) -> Subscription:
    subscription = Subscription(user_id=user.id, status=status.value)
    db_session.add(subscription)
    db_session.commit()
    db_session.refresh(user)
    return subscription


# ---------------------------------------------------------------- catalog


def test_every_plan_names_every_limit():
    # A plan that leaves a limit at None has promised "unlimited", which is
    # a decision. This asserts it is never made by accident — the free tier
    # in particular must cap everything it can.
    for entitlements in ENTITLEMENTS_BY_PLAN.values():
        for name in LIMIT_FIELDS:
            assert hasattr(entitlements, name)
    for name in LIMIT_FIELDS:
        assert limit_for(FREE_PLAN, name) is not None


def test_limit_fields_covers_every_field_that_is_not_a_label():
    labels = {"plan_key", "display_name"}
    declared = {field.name for field in fields(Entitlements)}
    assert set(LIMIT_FIELDS) == declared - labels


def test_every_priced_plan_has_entitlements():
    # The catalog endpoint skips a priced plan with no entitlements, so a
    # plan added to Stripe and not here would be sold and never listed.
    for plan in BILLING_PLANS:
        assert plan.key in ENTITLEMENTS_BY_PLAN, plan.key


def test_the_paid_plan_key_matches_the_price_catalog():
    assert PAID_PLAN_KEY in {plan.key for plan in BILLING_PLANS}
    assert PAID_PLAN_KEY in ENTITLEMENTS_BY_PLAN


def test_no_plan_promises_more_than_the_services_will_give():
    # A plan is a promise about the product; the service constants are what
    # the deployment can carry. A plan above them would be advertising
    # something the backstop silently clamps.
    for entitlements in ENTITLEMENTS_BY_PLAN.values():
        assert entitlements.max_active_api_keys <= MAX_ACTIVE_KEYS_PER_USER
        assert entitlements.max_active_sessions <= MAX_ACTIVE_SESSIONS_PER_USER
        assert entitlements.max_webhook_endpoints <= MAX_ENDPOINTS_PER_USER


def test_the_free_tier_is_strictly_below_the_paid_one():
    # Not decoration: a free tier that matched the paid one on any axis is a
    # paid tier nobody has a reason to buy.
    for name in LIMIT_FIELDS:
        assert limit_for(FREE_PLAN, name) < limit_for(PRO_PLAN, name), name


def test_an_unknown_plan_falls_back_to_free():
    # The safe direction: a subscription referencing a product this build
    # does not know about under-serves one customer rather than handing out
    # every ceiling in the product.
    assert entitlements_for_plan("enterprise_unicorn") is FREE_PLAN
    assert entitlements_for_plan(None) is FREE_PLAN


def test_limit_for_rejects_a_field_that_is_not_a_limit():
    with pytest.raises(KeyError):
        limit_for(FREE_PLAN, "display_name")


@pytest.mark.parametrize(
    ("limit", "used", "expected"),
    [
        (10, 3, 7),
        (10, 10, 0),
        # A downgrade can leave an account over its new ceiling. Clamped,
        # because "-3 remaining" is a number no interface can render.
        (2, 5, 0),
        (None, 900, None),
    ],
)
def test_remaining_clamps_at_zero_and_passes_through_unlimited(limit, used, expected):
    assert remaining(limit, used) == expected


@pytest.mark.parametrize(
    ("limit", "used", "expected"),
    [(2, 1, False), (2, 2, True), (2, 3, True), (None, 10_000, False)],
)
def test_is_exhausted(limit, used, expected):
    assert is_exhausted(limit, used) is expected


# ------------------------------------------------------------- resolution


def test_an_account_with_no_subscription_is_on_the_free_plan(user):
    assert plan_key_for(user) == FREE_PLAN_KEY
    assert entitlements_for(user) is FREE_PLAN


@pytest.mark.parametrize(
    "status", [SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING]
)
def test_an_entitling_subscription_grants_the_paid_plan(db_session, user, status):
    _subscribe(db_session, user, status)
    assert plan_key_for(user) == PAID_PLAN_KEY
    assert entitlements_for(user) is PRO_PLAN


@pytest.mark.parametrize(
    "status", [SubscriptionStatus.PAST_DUE, SubscriptionStatus.CANCELED]
)
def test_a_lapsed_subscription_falls_back_to_free(db_session, user, status):
    # past_due in particular: continuing to serve paid ceilings through an
    # indefinite dunning cycle is how a SaaS gives its product away.
    _subscribe(db_session, user, status)
    assert entitlements_for(user) is FREE_PLAN


def test_the_plan_follows_the_subscription_without_a_second_write(db_session, user):
    subscription = _subscribe(db_session, user, SubscriptionStatus.ACTIVE)
    assert entitlements_for(user) is PRO_PLAN

    subscription.status = SubscriptionStatus.CANCELED.value
    db_session.commit()
    db_session.refresh(user)

    # Derived, not stored: nothing had to be updated on the user for the
    # cancellation to take effect.
    assert entitlements_for(user) is FREE_PLAN


# ------------------------------------------------------------------ clock


def test_month_start_is_the_first_of_the_month_in_utc():
    assert month_start(datetime(2026, 8, 29, 17, 4, tzinfo=UTC)) == datetime(
        2026, 8, 1, tzinfo=UTC
    )


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 1, 15, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)),
        # February is the month that catches a "+31 days" implementation.
        (datetime(2026, 2, 3, tzinfo=UTC), datetime(2026, 3, 1, tzinfo=UTC)),
        (datetime(2026, 12, 31, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)),
    ],
)
def test_next_period_start_rolls_over_the_year_and_short_months(now, expected):
    assert next_period_start(now) == expected


# ----------------------------------------------------------------- quotas


def test_a_fresh_account_has_spent_nothing(db_session, user):
    snapshot = quota_snapshot(db_session, user)

    # Against QUOTA_KEYS rather than a literal list: a ceiling added to the
    # catalog should show up in the snapshot without this test needing a
    # hand, and should fail here if it is declared and never counted.
    assert [quota.key for quota in snapshot.quotas] == list(QUOTA_KEYS)
    assert all(quota.used == 0 for quota in snapshot.quotas)
    assert snapshot.subscription_status is None
    assert snapshot.period_start == month_start()


def test_the_snapshot_reports_the_subscription_behind_the_plan(db_session, user):
    _subscribe(db_session, user, SubscriptionStatus.PAST_DUE)

    snapshot = quota_snapshot(db_session, user)

    # Free plan, but the interface can say *why* — a declined card and a
    # never-subscribed account want different words.
    assert snapshot.entitlements is FREE_PLAN
    assert snapshot.subscription_status == SubscriptionStatus.PAST_DUE.value


def test_api_key_quota_counts_usable_keys_only(db_session, user):
    key, _ = create_api_key(db_session, user, name="First")
    assert api_key_quota(db_session, user).used == 1

    key.revoked_at = datetime.now(UTC)
    db_session.commit()

    # Revoked keys stay listed but stop counting, which is what makes the
    # ceiling manageable rather than a one-way door.
    assert api_key_quota(db_session, user).used == 0


def test_session_quota_counts_live_sessions(db_session, user):
    start_session(db_session, user, create_refresh_token(str(user.id)))

    quota = session_quota(db_session, user)

    assert quota.used == count_active_sessions(db_session, user) == 1
    assert quota.limit == FREE_PLAN.max_active_sessions


def test_event_quota_counts_only_this_calendar_month(db_session, user):
    now = datetime.now(UTC)
    db_session.add_all(
        [
            UsageEvent(user_id=user.id, name="a.b", occurred_at=now),
            # Comfortably inside the previous month whatever today's date is.
            UsageEvent(
                user_id=user.id,
                name="a.b",
                occurred_at=month_start(now) - timedelta(days=3),
            ),
        ]
    )
    db_session.commit()

    assert event_quota(db_session, user).used == 1


def test_event_quota_ignores_other_accounts(db_session, user):
    stranger = User(
        email="stranger@example.com", hashed_password=hash_password(PASSWORD)
    )
    db_session.add(stranger)
    db_session.commit()
    db_session.add(UsageEvent(user_id=stranger.id, name="a.b"))
    db_session.commit()

    assert event_quota(db_session, user).used == 0


def test_check_api_key_quota_raises_once_the_plan_is_spent(db_session, user):
    for index in range(FREE_PLAN.max_active_api_keys):
        create_api_key(
            db_session,
            user,
            name=f"Key {index}",
            max_active_keys=FREE_PLAN.max_active_api_keys,
        )

    with pytest.raises(QuotaExceededError) as raised:
        check_api_key_quota(db_session, user)

    # The exception carries the quota, so the endpoint can name the plan and
    # the number instead of saying "quota exceeded".
    assert raised.value.quota.limit == FREE_PLAN.max_active_api_keys
    assert FREE_PLAN.display_name in str(raised.value)


# -------------------------------------------------------------- allowance


def _quota(limit, used):
    return Quota(
        key="events", label="events this month", limit=limit, used=used,
        plan_display_name="Free",
    )


@pytest.mark.parametrize(
    ("limit", "used", "requested", "expected"),
    [
        (100, 0, 10, 10),
        # The whole point of clipping: a fixed-size flush that overshoots
        # takes the room that is left instead of failing entirely.
        (100, 95, 10, 5),
        (100, 100, 10, 0),
        # Already over, after a downgrade.
        (100, 140, 10, 0),
        (None, 9_000, 10, 10),
    ],
)
def test_allowance_clips_to_what_is_left(limit, used, requested, expected):
    assert _quota(limit, used).allowance(requested) == expected


def test_allowance_for_events_reads_the_account(db_session, user):
    db_session.add_all(
        [UsageEvent(user_id=user.id, name="a.b") for _ in range(3)]
    )
    db_session.commit()

    assert (
        allowance_for_events(db_session, user, 10) == 10
    ), "three of ten thousand spent leaves room for the whole batch"


def test_a_quota_renders_itself_for_a_refusal_message():
    assert "at most 2" in str(_quota(2, 2))
    assert "unlimited" in str(_quota(None, 2))


# ------------------------------------------------------------- backstops


@pytest.mark.parametrize(
    ("asked", "expected"),
    [
        (None, MAX_ACTIVE_KEYS_PER_USER),
        (2, 2),
        # A plan asking for more than the deployment carries is clamped
        # rather than believed.
        (MAX_ACTIVE_KEYS_PER_USER + 50, MAX_ACTIVE_KEYS_PER_USER),
        (-1, 0),
    ],
)
def test_effective_key_limit(asked, expected):
    assert effective_key_limit(asked) == expected


@pytest.mark.parametrize(
    ("asked", "expected"),
    [
        (None, MAX_ACTIVE_SESSIONS_PER_USER),
        (3, 3),
        (MAX_ACTIVE_SESSIONS_PER_USER + 50, MAX_ACTIVE_SESSIONS_PER_USER),
        # Floored at one: a cap of zero would retire the session being
        # created and sign the user out in the act of signing them in.
        (0, 1),
    ],
)
def test_effective_session_cap(asked, expected):
    assert effective_session_cap(asked) == expected


def test_the_session_cap_retires_the_oldest_at_the_plan_limit(db_session, user):
    started = [
        start_session(
            db_session,
            user,
            create_refresh_token(str(user.id)) + uuid.uuid4().hex,
            max_active_sessions=FREE_PLAN.max_active_sessions,
        )
        for _ in range(FREE_PLAN.max_active_sessions + 2)
    ]

    # A correct password is never refused because of housekeeping, and a
    # downgrade must not be able to lock somebody out of their own account.
    assert count_active_sessions(db_session, user) == FREE_PLAN.max_active_sessions
    assert not started[0].is_active
    assert started[-1].is_active

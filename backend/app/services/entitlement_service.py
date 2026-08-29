"""Which plan an account is on, and how much of it is left.

`app.core.entitlements` is the catalog — static, code-defined, the same for
everybody on a plan. This module is the part that needs a database: it maps
one account to its plan, counts what that account has actually used, and
answers the question the endpoints ask before they write anything.

Three things are worth stating outright.

**A plan is derived, never stored.** There is no `users.plan` column, and
adding one would be a second source of truth that drifts the first time a
webhook is missed. The plan is a function of the subscription row Stripe
already keeps in step, so a `past_due` account falls back to the free tier
the moment the webhook lands rather than when a nightly job notices.

**Reporting and enforcement are separate calls.** `quota_snapshot` builds
the whole picture for the dashboard and costs three counting queries;
`check_api_key_quota` costs one and is what a write path calls. A write
that paid for the full snapshot would be counting sessions and events to
find out whether it may mint a key.

**Exhausted is not an error condition.** Hitting a ceiling is the product
working as sold, so the failure is a 409 with the plan named in it, not a
500 and not a silent truncation. The one exception is event ingest, which
clips the batch and reports what it took — see `allowance_for_events`.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.entitlements import (
    FREE_PLAN_KEY,
    Entitlements,
    entitlements_for_plan,
    is_exhausted,
    remaining,
)
from app.core.timeutils import as_utc
from app.models.subscription import SubscriptionStatus
from app.models.user import User
from app.services.analytics_service import count_events
from app.services.api_key_service import active_key_count
from app.services.session_service import count_active_sessions

logger = logging.getLogger("app.entitlements")

# Statuses that actually grant the paid plan. `past_due` is deliberately
# absent: Stripe sets it when a renewal fails, and continuing to serve the
# paid ceilings through an indefinite dunning cycle is how a SaaS gives its
# product away. `trialing` is present for the opposite reason — a trial that
# does not include what is being trialled is not a trial.
ENTITLING_STATUSES = frozenset(
    {SubscriptionStatus.ACTIVE.value, SubscriptionStatus.TRIALING.value}
)

# The Stripe product a paid subscription maps to. A single-plan catalog, so
# this is a constant rather than a lookup — when a second paid plan lands,
# the subscription row grows a price id and this becomes a mapping. Kept in
# one place so that change is one line, not a search.
PAID_PLAN_KEY = "pro_monthly"

# The three ceilings an account can *consume*. `audit_history_days` is a
# window rather than a budget — nothing is used up by reading — so it is
# reported as a plain entitlement instead of as a quota with a meter.
QUOTA_KEYS = ("api_keys", "sessions", "events")


class QuotaExceededError(Exception):
    """A ceiling was reached, and the write that asked has to be refused.

    Carries the quota rather than just a message so the endpoint can name
    the limit and the plan in its response — "at most 2 keys on the Free
    plan" is actionable, "quota exceeded" is not.
    """

    def __init__(self, quota: "Quota") -> None:
        super().__init__(str(quota))
        self.quota = quota


@dataclass(frozen=True)
class Quota:
    """One ceiling and how much of it this account has spent."""

    key: str
    label: str
    limit: int | None
    used: int
    #: The plan the limit came from, so a message can say which plan is
    #: being enforced without the caller re-deriving it.
    plan_display_name: str

    @property
    def remaining(self) -> int | None:
        return remaining(self.limit, self.used)

    @property
    def exhausted(self) -> bool:
        return is_exhausted(self.limit, self.used)

    @property
    def unlimited(self) -> bool:
        return self.limit is None

    def allowance(self, requested: int) -> int:
        """How many of `requested` units this quota still has room for.

        Clipping rather than an all-or-nothing verdict, because the caller
        that needs this — event ingest — flushes a fixed-size buffer: an
        all-or-nothing answer would refuse every flush once the allowance
        got within one batch of full, losing the room the account still had.

        Zero is the caller's cue to refuse outright rather than to report a
        successful write of nothing.
        """
        if self.limit is None:
            return max(requested, 0)
        return max(min(requested, self.limit - self.used), 0)

    def __str__(self) -> str:
        if self.limit is None:
            return f"{self.label} is unlimited on the {self.plan_display_name} plan"
        return (
            f"The {self.plan_display_name} plan allows at most {self.limit} "
            f"{self.label} ({self.used} in use)"
        )


@dataclass(frozen=True)
class PlanStatus:
    """Everything the dashboard's plan panel renders, in one object."""

    entitlements: Entitlements
    #: The raw subscription status, or None when the account has no
    #: subscription row at all. Reported alongside the plan because "Free
    #: because you never subscribed" and "Free because your card was
    #: declined" want very different words in the interface.
    subscription_status: str | None
    quotas: list[Quota]
    #: When the monthly event allowance last reset. Shown so "9,904 of
    #: 10,000" carries the period it counts, which is the difference
    #: between a number and a number that means something.
    period_start: datetime


def _now() -> datetime:
    return datetime.now(UTC)


def month_start(now: datetime | None = None) -> datetime:
    """First moment of the current UTC calendar month.

    Calendar month rather than a rolling 30 days: the allowance is
    something a customer reconciles against an invoice, and an allowance
    that resets on a sliding window can never be reconciled against
    anything. UTC rather than the account's zone because there is no
    account zone, and inventing one would make the reset time depend on a
    field nobody sets.

    A caller's clock is converted rather than trusted, for two reasons that
    point the same way: a datetime in +09:00 is in a different *calendar
    month* from UTC for nine hours of every month boundary, and the result
    is bound into a SQL comparison against timestamps SQLite stores as naive
    UTC — where the driver would drop the offset instead of applying it.
    """
    now = as_utc(now) or _now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def next_period_start(now: datetime | None = None) -> datetime:
    """First moment of the *next* UTC calendar month.

    When a spent monthly allowance comes back. Returned to a refused caller
    as a Retry-After, which is a long time to be told to wait — and is
    exactly right: retrying sooner cannot succeed, and a client that backs
    off properly stops hammering an endpoint that has nothing to give it.
    """
    start = month_start(now)
    # Step into the next month by way of the 28th, which every month has:
    # adding 31 days would skip February entirely.
    return month_start(start.replace(day=28) + timedelta(days=7))


def plan_key_for(user: User) -> str:
    """The plan key this account is entitled to right now.

    Derived from the subscription's status rather than stored, so a
    cancellation takes effect when Stripe says so rather than when somebody
    remembers to update a column.
    """
    subscription = user.subscription
    if subscription is None or subscription.status not in ENTITLING_STATUSES:
        return FREE_PLAN_KEY
    return PAID_PLAN_KEY


def entitlements_for(user: User) -> Entitlements:
    """The ceilings this account is entitled to right now."""
    return entitlements_for_plan(plan_key_for(user))


def _quota(
    entitlements: Entitlements,
    key: str,
    label: str,
    limit: int | None,
    used: int,
) -> Quota:
    return Quota(
        key=key,
        label=label,
        limit=limit,
        used=used,
        plan_display_name=entitlements.display_name,
    )


def api_key_quota(
    db: Session, user: User, *, entitlements: Entitlements | None = None
) -> Quota:
    """How many more keys this account may hold.

    `entitlements` is accepted so a caller that already resolved the plan
    does not resolve it twice; it is cheap, but the snapshot builds three
    of these and the duplication reads as though it might not be.
    """
    entitlements = entitlements or entitlements_for(user)
    return _quota(
        entitlements,
        "api_keys",
        "active API keys",
        entitlements.max_active_api_keys,
        active_key_count(db, user),
    )


def session_quota(
    db: Session, user: User, *, entitlements: Entitlements | None = None
) -> Quota:
    """How many more devices may stay signed in.

    Reported rather than enforced by refusal: reaching this retires the
    oldest session instead of failing the login, for the reason
    `session_service` gives at its cap constant.
    """
    entitlements = entitlements or entitlements_for(user)
    return _quota(
        entitlements,
        "sessions",
        "signed-in devices",
        entitlements.max_active_sessions,
        count_active_sessions(db, user),
    )


def event_quota(
    db: Session,
    user: User,
    *,
    entitlements: Entitlements | None = None,
    now: datetime | None = None,
) -> Quota:
    """Product events recorded for this account so far this month."""
    entitlements = entitlements or entitlements_for(user)
    return _quota(
        entitlements,
        "events",
        "events this month",
        entitlements.monthly_event_allowance,
        count_events(db, since=month_start(now), user_id=user.id),
    )


def quota_snapshot(
    db: Session, user: User, *, now: datetime | None = None
) -> PlanStatus:
    """The whole plan picture for one account.

    Three counting queries, which is why the write paths call the single
    quota they care about instead of this.
    """
    now = now or _now()
    entitlements = entitlements_for(user)
    subscription = user.subscription

    return PlanStatus(
        entitlements=entitlements,
        subscription_status=subscription.status if subscription is not None else None,
        quotas=[
            api_key_quota(db, user, entitlements=entitlements),
            session_quota(db, user, entitlements=entitlements),
            event_quota(db, user, entitlements=entitlements, now=now),
        ],
        period_start=month_start(now),
    )


def check_api_key_quota(
    db: Session, user: User, *, entitlements: Entitlements | None = None
) -> Quota:
    """Refuse if this account may not mint another key.

    Returns the quota on success so the caller can report what is left
    without counting a second time.
    """
    quota = api_key_quota(db, user, entitlements=entitlements)
    if quota.exhausted:
        logger.info(
            "api key creation refused by plan quota",
            extra={"user_id": str(user.id), "plan": quota.plan_display_name},
        )
        raise QuotaExceededError(quota)
    return quota


def allowance_for_events(
    db: Session,
    user: User,
    requested: int,
    *,
    entitlements: Entitlements | None = None,
    now: datetime | None = None,
) -> int:
    """How many of `requested` events may be recorded, 0 to `requested`.

    Clipping rather than refusing the batch is the one place a ceiling does
    not produce an error, and it is because of how the client behaves: the
    SPA buffers events and flushes them together, so a fixed batch size
    meeting a nearly-spent allowance would fail every flush from then on and
    the account would lose the events it still had room for. The ingest
    endpoint already answers "how many were recorded" rather than "all of
    them", so clipping fits a contract that was there before this.

    Returning zero is the caller's signal to refuse outright, which it does
    with a 429 — there is nothing to accept and saying "accepted 0" would be
    a quieter way of dropping data.
    """
    return event_quota(db, user, entitlements=entitlements, now=now).allowance(
        requested
    )


__all__ = [
    "ENTITLING_STATUSES",
    "PAID_PLAN_KEY",
    "QUOTA_KEYS",
    "PlanStatus",
    "Quota",
    "QuotaExceededError",
    "allowance_for_events",
    "api_key_quota",
    "check_api_key_quota",
    "entitlements_for",
    "event_quota",
    "month_start",
    "next_period_start",
    "plan_key_for",
    "quota_snapshot",
    "session_quota",
]

"""Recording and aggregation of product usage events.

Two rules shape this module:

1. Recording must never break the request that triggered it. A failed
   insert into an analytics table is not a reason to fail a signup, so
   `record_event` swallows and logs database errors instead of raising.
2. Aggregation happens in SQL, not in Python. The dashboard reads windows
   of days over a table that only ever grows, so the queries group and
   count in the database and return one row per bucket.
"""

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.usage_event import EventName, EventSource, UsageEvent
from app.models.user import User

logger = logging.getLogger("app.analytics")

# A window longer than this is a reporting job, not a dashboard query.
MAX_WINDOW_DAYS = 365


@dataclass(frozen=True)
class DailyCount:
    """Events recorded on one UTC calendar day."""

    day: date
    count: int


@dataclass(frozen=True)
class UsageSummary:
    """Everything the admin usage panel shows, for one window."""

    window_days: int
    total_events: int
    active_users: int
    events_by_name: dict[str, int]
    daily: list[DailyCount]


def _now() -> datetime:
    return datetime.now(UTC)


def _window_start(days: int, now: datetime | None = None) -> datetime:
    """Start of an inclusive `days`-long window ending today.

    days=1 means "today only", so the window opens at the start of the UTC
    day `days - 1` days ago rather than exactly `days * 24h` in the past.
    Bucketing by calendar day and filtering by rolling hours would otherwise
    return a leading partial bucket.
    """
    now = now or _now()
    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_of_today - timedelta(days=max(days, 1) - 1)


def clamp_window(days: int) -> int:
    return max(1, min(days, MAX_WINDOW_DAYS))


def record_event(
    db: Session,
    name: str | EventName,
    *,
    user: User | None = None,
    user_id: uuid.UUID | None = None,
    source: EventSource | str = EventSource.BACKEND,
    properties: dict | None = None,
    occurred_at: datetime | None = None,
    commit: bool = True,
) -> UsageEvent | None:
    """Append one event, returning None if it was dropped.

    Dropped means either analytics is switched off for this deployment or
    the insert failed; callers treat both the same way, because neither is
    something the caller can act on.

    Pass `user` when the caller already holds the row, `user_id` when it
    only has the id — webhook handlers work from a subscription and would
    otherwise load a user purely to record an event.
    """
    if not get_settings().analytics_enabled:
        return None

    event = UsageEvent(
        user_id=user.id if user is not None else user_id,
        name=str(name),
        source=str(source),
        properties=properties or {},
        occurred_at=occurred_at or _now(),
    )

    if commit:
        try:
            db.add(event)
            db.commit()
        except SQLAlchemyError:
            # Analytics is best-effort: the caller's own work already
            # succeeded, and losing one event is preferable to turning a
            # signup into a 500.
            logger.exception(
                "failed to record usage event", extra={"event_name": str(name)}
            )
            db.rollback()
            return None
        return event

    # commit=False means the caller is folding this event into a transaction
    # of their own that they will commit themselves. A bare rollback here
    # would then discard *their* uncommitted work to clean up after a failed
    # analytics insert — precisely the outcome rule 1 of this module exists
    # to prevent, and a silent one, since the caller is told only that the
    # event was dropped.
    #
    # A SAVEPOINT scopes the undo to this insert: on failure the session
    # rewinds to the point before the event was added and the caller's
    # transaction survives, still open and still committable.
    try:
        with db.begin_nested():
            db.add(event)
    except SQLAlchemyError:
        logger.exception(
            "failed to record usage event", extra={"event_name": str(name)}
        )
        return None

    return event


def record_events(db: Session, events: Iterable[tuple[str, dict]], *, user: User | None = None,
                  source: EventSource | str = EventSource.FRONTEND) -> int:
    """Record a batch in one transaction; returns how many were stored.

    The SPA buffers events and flushes them together, so the common case is
    a handful of rows that should cost one round trip, not one each.

    All or nothing: the count returned is what the caller can rely on having
    been committed, so a failure anywhere in the batch reports zero rather
    than a number that a rollback has already taken back.
    """
    if not get_settings().analytics_enabled:
        return 0

    now = _now()
    user_id = user.id if user is not None else None
    rows = [
        UsageEvent(
            user_id=user_id,
            name=str(name),
            source=str(source),
            properties=properties or {},
            occurred_at=now,
        )
        for name, properties in events
    ]
    if not rows:
        return 0

    try:
        db.add_all(rows)
        db.commit()
    except SQLAlchemyError:
        # Best-effort, like record_event: the caller's own work is what
        # matters, and losing a batch of analytics is not worth a 500.
        logger.exception("failed to commit usage event batch")
        db.rollback()
        return 0

    return len(rows)


def _as_date(value: object) -> date:
    """Normalise whatever the driver returned for a grouped day.

    SQLite hands back an ISO string from date(), Postgres a date object.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def count_events(
    db: Session,
    *,
    since: datetime | None = None,
    name: str | None = None,
    user_id=None,
) -> int:
    stmt = select(func.count()).select_from(UsageEvent)
    if since is not None:
        stmt = stmt.where(UsageEvent.occurred_at >= since)
    if name is not None:
        stmt = stmt.where(UsageEvent.name == name)
    if user_id is not None:
        stmt = stmt.where(UsageEvent.user_id == user_id)
    return int(db.execute(stmt).scalar_one())


def count_active_users(db: Session, *, since: datetime) -> int:
    """Distinct accounts that produced at least one event in the window.

    Anonymous events carry no user_id and are excluded, so this counts
    accounts rather than sessions.
    """
    stmt = (
        select(func.count(func.distinct(UsageEvent.user_id)))
        .where(UsageEvent.occurred_at >= since)
        .where(UsageEvent.user_id.is_not(None))
    )
    return int(db.execute(stmt).scalar_one())


def counts_by_name(db: Session, *, since: datetime, user_id=None) -> dict[str, int]:
    stmt = (
        select(UsageEvent.name, func.count())
        .where(UsageEvent.occurred_at >= since)
        .group_by(UsageEvent.name)
        .order_by(func.count().desc(), UsageEvent.name)
    )
    if user_id is not None:
        stmt = stmt.where(UsageEvent.user_id == user_id)
    return {name: int(count) for name, count in db.execute(stmt).all()}


def daily_counts(
    db: Session,
    *,
    days: int,
    name: str | None = None,
    user_id=None,
    now: datetime | None = None,
) -> list[DailyCount]:
    """One entry per UTC day in the window, oldest first.

    Days with no events are filled in with zero: a sparkline with gaps in
    it reads as missing data rather than as quiet days.
    """
    days = clamp_window(days)
    now = now or _now()
    start = _window_start(days, now)

    day_column = func.date(UsageEvent.occurred_at)
    stmt = (
        select(day_column, func.count())
        .where(UsageEvent.occurred_at >= start)
        .group_by(day_column)
    )
    if name is not None:
        stmt = stmt.where(UsageEvent.name == name)
    if user_id is not None:
        stmt = stmt.where(UsageEvent.user_id == user_id)

    counted = {_as_date(day): int(count) for day, count in db.execute(stmt).all()}

    first_day = start.date()
    return [
        DailyCount(day=first_day + timedelta(days=offset),
                   count=counted.get(first_day + timedelta(days=offset), 0))
        for offset in range(days)
    ]


def summarize_usage(db: Session, *, days: int = 14, now: datetime | None = None) -> UsageSummary:
    """Account-wide usage over the window, for the admin panel."""
    days = clamp_window(days)
    now = now or _now()
    since = _window_start(days, now)

    return UsageSummary(
        window_days=days,
        total_events=count_events(db, since=since),
        active_users=count_active_users(db, since=since),
        events_by_name=counts_by_name(db, since=since),
        daily=daily_counts(db, days=days, now=now),
    )


def summarize_user_usage(
    db: Session, user: User, *, days: int = 14, now: datetime | None = None
) -> UsageSummary:
    """The same shape as summarize_usage, scoped to one account.

    active_users is 1 or 0 here — kept in the shape so the frontend can
    render either summary with one component.
    """
    days = clamp_window(days)
    now = now or _now()
    since = _window_start(days, now)
    total = count_events(db, since=since, user_id=user.id)

    return UsageSummary(
        window_days=days,
        total_events=total,
        active_users=1 if total else 0,
        events_by_name=counts_by_name(db, since=since, user_id=user.id),
        daily=daily_counts(db, days=days, user_id=user.id, now=now),
    )


def recent_events(db: Session, *, user_id=None, limit: int = 20) -> list[UsageEvent]:
    stmt = select(UsageEvent).order_by(UsageEvent.occurred_at.desc()).limit(limit)
    if user_id is not None:
        stmt = stmt.where(UsageEvent.user_id == user_id)
    return list(db.execute(stmt).scalars().all())


def prune_events(db: Session, *, older_than_days: int, now: datetime | None = None) -> int:
    """Delete events older than the retention window; returns rows removed.

    An append-only table needs somebody to take the other side of the deal.
    Run this on a schedule (see app/scripts/prune_usage_events.py).
    """
    now = now or _now()
    cutoff = now - timedelta(days=max(older_than_days, 1))
    deleted = (
        db.query(UsageEvent)
        .filter(UsageEvent.occurred_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info("pruned usage events", extra={"deleted": deleted, "cutoff": cutoff.isoformat()})
    return int(deleted)


__all__ = [
    "DailyCount",
    "UsageSummary",
    "EventName",
    "EventSource",
    "clamp_window",
    "count_active_users",
    "count_events",
    "counts_by_name",
    "daily_counts",
    "prune_events",
    "recent_events",
    "record_event",
    "record_events",
    "summarize_usage",
    "summarize_user_usage",
]

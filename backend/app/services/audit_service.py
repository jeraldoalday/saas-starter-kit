"""Recording and reading the security audit trail.

Three rules shape this module, and the rest of it follows from them.

1. **Recording must not break the request that triggered it.** A login that
   succeeds and then 500s because an audit insert failed has made the
   account *less* usable in the name of security. So a failed write is
   swallowed here, exactly as `analytics_service` swallows a dropped event.

2. **A dropped row is an operational failure, not a shrug.** That is where
   this parts company with analytics. Losing a usage event costs a wobble in
   a chart; losing an audit row costs the answer to "what happened on the
   3rd", and nobody finds out until they need it. So a swallowed failure is
   logged at exception level *and* raised as an alert through the on-call
   router, where the suppression window keeps a sustained outage from
   drowning the channel.

3. **The trail records who and where, never what was typed.** A failed login
   against an address nobody registered is stored with no user at all rather
   than with the address that was attempted. Storing it would build an
   inventory of every string typed into an email box — including passwords
   typed into the wrong field, and near-misses of real customers' addresses —
   and would turn a table the owner can read into an enumeration oracle. The
   IP and the User-Agent are enough to see a spray attack; the exact address
   adds nothing an operator can act on.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.timeutils import as_utc
from app.models.audit_log import (
    MAX_IP_LENGTH,
    MAX_USER_AGENT_LENGTH,
    AuditAction,
    AuditLog,
    AuditOutcome,
)
from app.models.user import User
from app.services.alerts import Severity, notify

logger = logging.getLogger("app.audit")

# A page of trail. Generous enough that the common case is one request,
# bounded so a caller cannot ask for the whole table in one go.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# A window longer than this is a reporting job, not a dashboard query — the
# same ceiling analytics_service puts on its own windows.
MAX_WINDOW_DAYS = 365


def _now() -> datetime:
    return datetime.now(UTC)


def _truncate(value: str | None, limit: int) -> str | None:
    """Clip client-supplied context to what the column holds.

    Truncated rather than rejected, for the reason `session_service` gives:
    a header nobody validates must not be able to fail the request it came
    in on — and here it must not be able to suppress an audit row either.
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped[:limit]


def record(
    db: Session,
    action: AuditAction | str,
    *,
    user: User | None = None,
    user_id: uuid.UUID | None = None,
    actor: User | None = None,
    actor_user_id: uuid.UUID | None = None,
    outcome: AuditOutcome | str = AuditOutcome.SUCCESS,
    ip_address: str | None = None,
    user_agent: str | None = None,
    context: dict[str, Any] | None = None,
) -> AuditLog | None:
    """Append one audit row, returning None if it could not be written.

    Pass `user` when the caller already holds the row and `user_id` when it
    only has the id — the deletion path has an id and no row left to load,
    and a webhook handler should not fetch a user purely to name one.

    `actor` is only worth setting when it differs from the subject: an admin
    deactivating a member, an owner granting a role. An account acting on
    itself leaves it null rather than repeating the subject, so "somebody
    else did this to me" is a non-null check instead of a comparison.

    Deliberately not gated on ANALYTICS_ENABLED. That flag governs product
    measurement, and a deployment that turns its funnel off has not asked to
    stop recording who signed in.
    """
    entry = AuditLog(
        user_id=user.id if user is not None else user_id,
        actor_user_id=actor.id if actor is not None else actor_user_id,
        action=str(action),
        outcome=str(outcome),
        ip_address=_truncate(ip_address, MAX_IP_LENGTH),
        user_agent=_truncate(user_agent, MAX_USER_AGENT_LENGTH),
        context=context or {},
        created_at=_now(),
    )

    try:
        db.add(entry)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        _report_dropped_row(action)
        return None

    return entry


def _report_dropped_row(action: AuditAction | str) -> None:
    """Make a lost audit row loud, since the reader cannot see the gap.

    Both channels on purpose: the log line is what an investigator finds
    later when the trail has a hole in it, and the alert is what stops the
    hole growing for a week first. Alerting is best-effort in turn — the
    on-call transport failing must not be what finally raises the exception
    this function exists to have swallowed.
    """
    logger.exception("failed to record audit entry", extra={"action": str(action)})
    try:
        notify(
            "Audit trail write failed",
            f"An audit entry for {action} could not be written. "
            "The security trail is incomplete for this period.",
            severity=Severity.WARNING,
            key="audit.write_failed",
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("failed to raise an alert for a dropped audit entry")


def clamp_page_size(limit: int) -> int:
    return max(1, min(limit, MAX_PAGE_SIZE))


def clamp_window(days: int) -> int:
    return max(1, min(days, MAX_WINDOW_DAYS))


def _filtered(
    stmt: Select,
    *,
    user_id: uuid.UUID | None = None,
    actions: list[str] | None = None,
    outcome: str | None = None,
    since: datetime | None = None,
) -> Select:
    """Apply the filters shared by the listing and counting queries.

    Shared so a page and its total can never disagree about what is being
    counted, which is the way paginated listings usually go wrong.
    """
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if actions:
        stmt = stmt.where(AuditLog.action.in_(actions))
    if outcome is not None:
        stmt = stmt.where(AuditLog.outcome == outcome)
    if since is not None:
        stmt = stmt.where(AuditLog.created_at >= since)
    return stmt


def list_entries(
    db: Session,
    *,
    user_id: uuid.UUID | None = None,
    actions: list[str] | None = None,
    outcome: str | None = None,
    since: datetime | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> list[AuditLog]:
    """A page of the trail, newest first.

    Ordered by `created_at` and then by `id`. The tiebreak matters more here
    than it looks: several rows share a timestamp whenever one request
    records more than one event, and without a stable second key those rows
    can reappear on the next page or vanish between two.
    """
    stmt = _filtered(
        select(AuditLog),
        user_id=user_id,
        actions=actions,
        outcome=outcome,
        since=since,
    )
    stmt = (
        stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(clamp_page_size(limit))
        .offset(max(offset, 0))
    )
    return list(db.execute(stmt).scalars().all())


def count_entries(
    db: Session,
    *,
    user_id: uuid.UUID | None = None,
    actions: list[str] | None = None,
    outcome: str | None = None,
    since: datetime | None = None,
) -> int:
    """How many rows match — the total behind a page of `list_entries`."""
    stmt = _filtered(
        select(func.count()).select_from(AuditLog),
        user_id=user_id,
        actions=actions,
        outcome=outcome,
        since=since,
    )
    return int(db.execute(stmt).scalar_one())


def list_for_user(
    db: Session,
    user: User,
    *,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> list[AuditLog]:
    """One account's own trail.

    Scoped by `user_id` — the subject — rather than by actor, so a member
    sees the admin action taken *against* their account. That is the whole
    point of showing someone their own trail: an unexplained role change is
    exactly the row they need to see.
    """
    return list_entries(db, user_id=user.id, limit=limit, offset=offset)


def failed_login_count(
    db: Session, *, since: datetime | None = None, hours: int = 1
) -> int:
    """Failed sign-ins across the deployment in the recent window.

    A single number rather than a breakdown, because the question it answers
    is binary — "is somebody spraying us right now?" — and the trail itself
    is where anyone goes next.
    """
    since = since or (_now() - timedelta(hours=max(hours, 1)))
    return count_entries(
        db, actions=[AuditAction.LOGIN_FAILED.value], since=since
    )


def counts_by_action(db: Session, *, since: datetime) -> dict[str, int]:
    """How often each action occurred in the window, busiest first."""
    stmt = (
        select(AuditLog.action, func.count())
        .where(AuditLog.created_at >= since)
        .group_by(AuditLog.action)
        .order_by(func.count().desc(), AuditLog.action)
    )
    return {action: int(count) for action, count in db.execute(stmt).all()}


def purge_audit_logs(
    db: Session, *, older_than_days: int, now: datetime | None = None
) -> int:
    """Delete trail older than the retention window; returns rows removed.

    An append-only table needs somebody to take the other side of the deal,
    the same bargain `prune_events` describes. The window here is longer
    than the session one and shorter than the analytics one: long enough
    that an incident noticed months late still has a trail, short enough
    that the table is not an indefinite store of people's IP addresses.

    The clock is normalised to UTC before the cutoff is bound. SQLite stores
    naive wall-clock UTC and its driver ignores tzinfo entirely, so a caller
    passing an aware datetime in any other zone would have its wall-clock
    reading compared as though it were UTC — off by the offset, silently,
    and only on the dialect the tests run on. `count_all_active_sessions`
    documents the same hazard at length.
    """
    now = as_utc(now) or _now()
    cutoff = now - timedelta(days=max(older_than_days, 1))
    deleted = (
        db.query(AuditLog)
        .filter(AuditLog.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info(
        "pruned audit logs",
        extra={"deleted": int(deleted), "cutoff": cutoff.isoformat()},
    )
    return int(deleted)


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "MAX_WINDOW_DAYS",
    "AuditAction",
    "AuditOutcome",
    "clamp_page_size",
    "clamp_window",
    "count_entries",
    "counts_by_action",
    "failed_login_count",
    "list_entries",
    "list_for_user",
    "purge_audit_logs",
    "record",
]

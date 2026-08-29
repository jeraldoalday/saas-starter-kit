import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_admin
from app.models.audit_log import AuditOutcome
from app.models.user import User
from app.schemas.audit import (
    AdminAuditEntryRead,
    AdminAuditPage,
    AuditEntryRead,
    AuditPage,
    AuditSummary,
)
from app.services.audit_service import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    MAX_WINDOW_DAYS,
    clamp_window,
    count_entries,
    counts_by_action,
    failed_login_count,
    list_entries,
)
from app.services.entitlement_service import entitlements_for

# Gated on get_current_user (a session JWT) rather than on the key-or-session
# dependency, for the reason api_keys and sessions give: a leaked API key can
# read and write the product, but it must not be able to read the owner's
# security history — which is a list of the addresses they sign in from and
# a map of when they are not watching.
router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/me", response_model=AuditPage)
def read_my_trail(
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AuditPage:
    """This account's own security history, newest first.

    Scoped to the account as the *subject*, so an action an administrator
    took against it appears here. That is the point of showing someone their
    own trail: an unexplained role change or deactivation is exactly the row
    they need to find without having to ask anybody.

    How far back it reaches is a plan entitlement. That bounds *reading*,
    not recording: rows are kept for AUDIT_RETENTION_DAYS regardless of who
    is paying, so upgrading reveals history that already exists rather than
    starting a clock. The window is reported back in `history_days`, because
    a trail that stops after thirty days with no explanation reads as data
    somebody deleted.

    The floor is applied to the count as well as to the page. Paginating a
    window against a total for the whole table is how a listing ends up
    promising a page nine that returns nothing.
    """
    history_days = entitlements_for(current_user).audit_history_days
    since = (
        datetime.now(UTC) - timedelta(days=history_days)
        if history_days is not None
        else None
    )

    entries = list_entries(
        db, user_id=current_user.id, since=since, limit=limit, offset=offset
    )
    return AuditPage(
        entries=[AuditEntryRead.from_entry(entry) for entry in entries],
        total=count_entries(db, user_id=current_user.id, since=since),
        limit=limit,
        offset=offset,
        history_days=history_days,
    )


@router.get("", response_model=AdminAuditPage)
def read_trail(
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    action: list[str] | None = Query(
        default=None,
        description="Restrict to these actions. Repeat the parameter to pass several.",
    ),
    outcome: AuditOutcome | None = Query(
        default=None, description="Restrict to successes or to refusals."
    ),
    user_id: uuid.UUID | None = Query(
        default=None, description="Restrict to one account's trail."
    ),
    days: int | None = Query(
        default=None,
        ge=1,
        le=MAX_WINDOW_DAYS,
        description="Only entries from the last N days.",
    ),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminAuditPage:
    """The whole deployment's trail, for reconstructing an incident.

    Admin rather than owner: reading the trail is how an on-call responder
    finds out what happened, and gating that behind the single highest role
    would mean waking somebody up to look at a list.

    Unrecognised action names are not rejected. They simply match nothing,
    which is the same answer a real action with no rows gives — and refusing
    them would mean an action retired in a later version breaks a saved
    filter rather than quietly returning empty.
    """
    since = (
        datetime.now(UTC) - timedelta(days=clamp_window(days))
        if days is not None
        else None
    )
    filters = {
        "actions": action,
        "outcome": outcome.value if outcome is not None else None,
        "user_id": user_id,
        "since": since,
    }

    entries = list_entries(db, limit=limit, offset=offset, **filters)
    return AdminAuditPage(
        entries=[AdminAuditEntryRead.from_entry(entry) for entry in entries],
        # Counted with the same filters as the page, so "showing 50 of 812"
        # cannot describe a different set from the 50 being shown.
        total=count_entries(db, **filters),
        limit=limit,
        offset=offset,
    )


@router.get("/summary", response_model=AuditSummary)
def summarize(
    days: int = Query(default=7, ge=1, le=MAX_WINDOW_DAYS),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AuditSummary:
    """Security activity over a window, as one screenful.

    `failed_logins` is broken out of the breakdown rather than left to be
    read off it, because it is the one number that answers a question on its
    own: whether somebody is spraying the login endpoint right now.
    """
    days = clamp_window(days)
    since = datetime.now(UTC) - timedelta(days=days)

    return AuditSummary(
        window_days=days,
        total_entries=count_entries(db, since=since),
        failed_logins=failed_login_count(db, since=since),
        entries_by_action=counts_by_action(db, since=since),
    )

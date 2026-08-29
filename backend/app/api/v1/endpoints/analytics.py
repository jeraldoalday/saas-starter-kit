import uuid
from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps_rate_limit import analytics_ingest_rate_limit
from app.core.config import get_settings
from app.core.entitlements import remaining as remaining_of
from app.database import get_db
from app.deps import get_api_or_session_user, require_admin
from app.models.usage_event import EventSource
from app.models.user import User
from app.schemas.analytics import (
    EventBatchIngest,
    IngestResponse,
    UsageEventRead,
    UsageSummaryRead,
)
from app.services.analytics_service import (
    MAX_WINDOW_DAYS,
    recent_events,
    record_events,
    summarize_usage,
    summarize_user_usage,
)
from app.services.entitlement_service import event_quota, next_period_start

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post(
    "/events",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(analytics_ingest_rate_limit)],
)
def ingest_events(
    data: EventBatchIngest,
    current_user: User = Depends(get_api_or_session_user),
    db: Session = Depends(get_db),
) -> IngestResponse:
    """Accept a batch of client events for the calling account.

    202 rather than 201: recording is best-effort, so the response says the
    batch was accepted, not that every row landed. `recorded` reports how
    many actually did — zero when analytics is switched off for the
    deployment, which is not an error the client can do anything about.

    Two different ceilings can bite here, and they mean different things.
    The batch-size limit is about this request being too big to process, so
    it is a 413 and the client should split and retry immediately. The plan
    allowance is about the account being out of budget for the month, so it
    is a 429 with a Retry-After pointing at the reset — retrying sooner
    cannot succeed.

    An allowance that runs out *mid-batch* clips rather than refuses, for
    the reason `Quota.allowance` gives: a client flushing a fixed-size
    buffer would otherwise lose the room it still had. `dropped` is how the
    client finds out, which is why it must not be ignored.
    """
    settings = get_settings()
    if len(data.events) > settings.analytics_max_batch_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"At most {settings.analytics_max_batch_size} events per request",
        )

    quota = event_quota(db, current_user)
    allowed = quota.allowance(len(data.events))
    if allowed == 0:
        resets_at = next_period_start()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"{quota}. The allowance resets on "
                f"{resets_at.date().isoformat()}."
            ),
            headers={
                "Retry-After": str(
                    max(int((resets_at - datetime.now(UTC)).total_seconds()), 1)
                )
            },
        )

    recorded = record_events(
        db,
        [(event.name, event.properties) for event in data.events[:allowed]],
        user=current_user,
        source=EventSource.FRONTEND,
    )
    return IngestResponse(
        recorded=recorded,
        dropped=len(data.events) - allowed,
        remaining=remaining_of(quota.limit, quota.used + recorded),
    )


@router.get("/me", response_model=UsageSummaryRead)
def my_usage(
    days: int = Query(default=14, ge=1, le=MAX_WINDOW_DAYS),
    current_user: User = Depends(get_api_or_session_user),
    db: Session = Depends(get_db),
) -> UsageSummaryRead:
    """The caller's own activity. No special role needed: it is their data."""
    summary = summarize_user_usage(db, current_user, days=days)
    # asdict recurses into the nested DailyCount dataclasses; vars() would
    # hand Pydantic a list of dataclass instances it can't validate.
    return UsageSummaryRead(**asdict(summary))


@router.get("/summary", response_model=UsageSummaryRead)
def usage_summary(
    days: int = Query(default=14, ge=1, le=MAX_WINDOW_DAYS),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UsageSummaryRead:
    """Account-wide usage. Admin-only: it aggregates every user's activity."""
    summary = summarize_usage(db, days=days)
    return UsageSummaryRead(**asdict(summary))


@router.get("/events", response_model=list[UsageEventRead])
def list_events(
    limit: int = Query(default=50, ge=1, le=200),
    user_id: uuid.UUID | None = Query(default=None),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[UsageEventRead]:
    """Raw event feed, newest first — the debugging view behind the summary."""
    return recent_events(db, user_id=user_id, limit=limit)

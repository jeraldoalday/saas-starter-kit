import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps_rate_limit import analytics_ingest_rate_limit
from app.core.config import get_settings
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
    """
    settings = get_settings()
    if len(data.events) > settings.analytics_max_batch_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"At most {settings.analytics_max_batch_size} events per request",
        )

    recorded = record_events(
        db,
        [(event.name, event.properties) for event in data.events],
        user=current_user,
        source=EventSource.FRONTEND,
    )
    return IngestResponse(recorded=recorded)


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

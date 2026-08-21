import logging

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.alerts import Severity, notify

logger = logging.getLogger("app.health")
router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict[str, str]:
    """Kept for backward compatibility; equivalent to /health/live."""
    return {"status": "ok"}


@router.get("/health/live")
def liveness() -> dict[str, str]:
    """Is the process up? No dependency checks - just that it can respond.

    An orchestrator restarts the container when this fails, so it must only
    fail for problems a restart would actually fix (a hung process), not for
    a dependency being temporarily down.
    """
    return {"status": "ok"}


@router.get("/health/ready")
def readiness(response: Response, db: Session = Depends(get_db)) -> dict[str, str]:
    """Can this instance serve traffic? Checks the database is reachable.

    An orchestrator stops routing to an instance when this fails, without
    restarting it - the right response to a dependency outage the process
    itself can't fix.
    """
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("readiness check failed: database unreachable")
        # Suppression matters here more than anywhere: an orchestrator
        # probes this every few seconds, and an outage would otherwise send
        # one notification per probe for as long as it lasted.
        notify(
            "Readiness check failing",
            "The database is unreachable, so this instance is refusing traffic.",
            severity=Severity.CRITICAL,
            key="health.database_unreachable",
        )
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "reason": "database"}

    return {"status": "ok"}

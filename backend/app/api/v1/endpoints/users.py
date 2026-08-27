from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.request_info import request_origin
from app.database import get_db
from app.deps import get_api_or_session_user, get_current_user
from app.models.audit_log import AuditAction
from app.models.usage_event import EventName
from app.models.user import User
from app.schemas.user import DeleteAccountRequest, UserRead
from app.services.account_service import (
    InvalidPasswordError,
    delete_account,
    export_user_data,
    has_active_subscription,
)
from app.services.analytics_service import record_event
from app.services.audit_service import record as record_audit

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
def read_current_user(
    current_user: User = Depends(get_api_or_session_user),
) -> UserRead:
    """The calling account. Reachable with a session or an API key.

    Doubles as the credential check a client can call once at startup to
    find out whether its key still works."""
    return current_user


@router.get("/me/export")
def export_my_data(
    request: Request,
    current_user: User = Depends(get_api_or_session_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Everything held about the caller, for a data-access request."""
    export = export_user_data(db, current_user)
    # Audited because this is the one route that hands the whole account
    # over in a single response. A stolen credential used to take a copy
    # leaves no other trace, and the export cannot include the row
    # describing itself.
    record_audit(
        db, AuditAction.ACCOUNT_EXPORTED, user=current_user, **request_origin(request)
    )
    return export


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_account(
    data: DeleteAccountRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    if has_active_subscription(current_user):
        # Deleting our row would not stop Stripe billing them.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cancel your subscription before deleting your account",
        )

    try:
        delete_account(db, current_user, data.password)
    except InvalidPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Password is incorrect"
        ) from exc

    # Deliberately anonymous: the account's own events were just cascaded
    # away with it, and attaching this one to the deleted id would either
    # fail the foreign key or resurrect a record of someone who asked to be
    # forgotten. Churn is still countable without knowing who churned.
    record_event(db, EventName.ACCOUNT_DELETED)
    # Anonymous for the same reason, and additionally because the
    # account's own trail was just erased along with it. What survives is
    # the fact that an erasure happened, which is what an operator needs
    # to reconcile a vanished account against.
    record_audit(db, AuditAction.ACCOUNT_DELETED, **request_origin(request))

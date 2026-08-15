from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.schemas.user import DeleteAccountRequest, UserRead
from app.services.account_service import (
    InvalidPasswordError,
    delete_account,
    export_user_data,
    has_active_subscription,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
def read_current_user(current_user: User = Depends(get_current_user)) -> UserRead:
    return current_user


@router.get("/me/export")
def export_my_data(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Everything held about the caller, for a data-access request."""
    return export_user_data(db, current_user)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_account(
    data: DeleteAccountRequest,
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

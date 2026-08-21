import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin, require_owner
from app.models.role import Role, rank
from app.models.usage_event import EventName
from app.models.user import User
from app.schemas.admin import AlertTestRequest, AlertTestResponse
from app.schemas.user import UserRead, UserRoleUpdate, UserStatusUpdate
from app.services.alerts import Severity, get_alert_router, notify
from app.services.analytics_service import record_event

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=list[UserRead])
def list_users(
    limit: int = Query(default=50, le=200, ge=1),
    offset: int = Query(default=0, ge=0),
    is_active: bool | None = Query(
        default=None, description="Filter to active or deactivated accounts."
    ),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[UserRead]:
    query = db.query(User)
    if is_active is not None:
        query = query.filter(User.is_active.is_(is_active))
    return query.order_by(User.created_at.desc()).limit(limit).offset(offset).all()


@router.patch("/users/{user_id}/role", response_model=UserRead)
def update_user_role(
    user_id: uuid.UUID,
    data: UserRoleUpdate,
    current_user: User = Depends(require_owner),
    db: Session = Depends(get_db),
) -> UserRead:
    """Change a user's role. Owner-only: this grants privilege."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    if user.id == current_user.id and data.role is not Role.OWNER:
        # Guard against an owner demoting themselves and leaving the account
        # with no one able to grant roles back.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Owners cannot demote themselves",
        )

    user.role = data.role.value
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}/status", response_model=UserRead)
def update_user_status(
    user_id: uuid.UUID,
    data: UserStatusUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserRead:
    """Switch an account off, or back on.

    Deactivation is the reversible counterpart to deletion: the rows stay,
    the sessions stop. Login refuses a deactivated account and every other
    entry point already checks is_active, so this takes effect on the next
    request rather than after the current access token expires.
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    if user.id == current_user.id:
        # An admin who switches themselves off cannot switch themselves back
        # on, and would need another admin to undo it.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot change your own account status",
        )

    if rank(user.role) >= rank(current_user.role):
        # Otherwise an admin could disable an owner, or their own peers, and
        # escalate by locking everyone else out.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot change the status of an account at or above your role",
        )

    was_active = user.is_active
    user.is_active = data.is_active
    db.commit()
    db.refresh(user)

    if was_active != user.is_active:
        record_event(
            db,
            EventName.ACCOUNT_REACTIVATED if user.is_active else EventName.ACCOUNT_DEACTIVATED,
            user_id=user.id,
            properties={"by_role": current_user.role},
        )

    return user


@router.post("/alerts/test", response_model=AlertTestResponse)
def send_test_alert(
    data: AlertTestRequest,
    current_user: User = Depends(require_owner),
    db: Session = Depends(get_db),
) -> AlertTestResponse:
    """Push one alert through the real routing path.

    Alert configuration is the kind that is only ever exercised during an
    incident, which is the worst possible time to discover the webhook URL
    has a typo in it. Owner-only, because it sends a real notification to
    whoever is on call.

    The suppression window is deliberately bypassed: two test alerts a
    minute apart should both arrive, or the test tells you nothing the
    second time.
    """
    router_ = get_alert_router()
    router_.reset()
    delivered = notify(
        "Test alert",
        data.message,
        severity=Severity.CRITICAL,
        key="admin.test_alert",
        requested_by=str(current_user.id),
    )
    return AlertTestResponse(delivered=delivered, backend=type(router_.sender).__name__)

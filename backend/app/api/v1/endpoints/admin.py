import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.request_info import request_origin
from app.database import get_db
from app.deps import require_admin, require_owner
from app.models.audit_log import AuditAction
from app.models.role import Role, rank
from app.models.subscription import Subscription
from app.models.usage_event import EventName
from app.models.user import User
from app.schemas.admin import AdminOverview, AlertTestRequest, AlertTestResponse
from app.schemas.user import UserRead, UserRoleUpdate, UserStatusUpdate
from app.services.alerts import Severity, get_alert_router, notify
from app.services.analytics_service import record_event
from app.services.api_key_service import count_keys
from app.services.audit_service import count_entries, failed_login_count
from app.services.audit_service import record as record_audit
from app.services.session_service import count_all_active_sessions, count_sessions
from app.services.webhooks.dispatch import pending_count
from app.services.webhooks.endpoints import count_disabled_endpoints, count_endpoints

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/overview", response_model=AdminOverview)
def overview(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminOverview:
    """Deployment-wide counts, for the question "is anything obviously wrong".

    Every figure is a COUNT in the database rather than rows loaded and
    counted in Python — this is the one endpoint whose cost scales with the
    whole deployment rather than with one account, so it must not be the
    thing that falls over first.

    That now includes the active-session figure, which used to be the
    exception: it loaded every row in auth_sessions and summed a property
    in Python, so the one endpoint that must not fall over first was the
    one holding the whole table in memory. `count_all_active_sessions`
    restates liveness as a WHERE clause instead.
    """

    def _count(model, *conditions) -> int:
        stmt = select(func.count()).select_from(model)
        for condition in conditions:
            stmt = stmt.where(condition)
        return int(db.execute(stmt).scalar_one())

    by_status = {
        status_name: int(count)
        for status_name, count in db.execute(
            select(Subscription.status, func.count()).group_by(Subscription.status)
        ).all()
    }

    return AdminOverview(
        total_users=_count(User),
        active_users=_count(User, User.is_active.is_(True)),
        verified_users=_count(User, User.is_verified.is_(True)),
        total_api_keys=count_keys(db),
        total_sessions=count_sessions(db),
        active_sessions=count_all_active_sessions(db),
        subscriptions_by_status=by_status,
        webhook_endpoints=count_endpoints(db),
        pending_webhook_deliveries=pending_count(db),
        disabled_webhook_endpoints=count_disabled_endpoints(db),
        failed_logins_last_hour=failed_login_count(db, hours=1),
        audit_entries=count_entries(db),
    )


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
    request: Request,
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

    previous_role = user.role
    user.role = data.role.value
    db.commit()
    db.refresh(user)

    if previous_role != user.role:
        # Filed against the *subject*, with the owner as actor, so it lands
        # in the trail of the person whose privileges changed. Somebody
        # discovering they are suddenly an admin — or suddenly not — should
        # find the answer in their own history rather than having to ask.
        record_audit(
            db,
            AuditAction.ROLE_CHANGED,
            user=user,
            actor=current_user,
            context={"from": previous_role, "to": user.role},
            **request_origin(request),
        )

    return user


@router.patch("/users/{user_id}/status", response_model=UserRead)
def update_user_status(
    user_id: uuid.UUID,
    data: UserStatusUpdate,
    request: Request,
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
        # The analytics event above records the role that acted; the audit
        # row records *who*. Churn is countable from the first, but "who
        # switched my account off" is only answerable from the second.
        record_audit(
            db,
            AuditAction.STATUS_CHANGED,
            user=user,
            actor=current_user,
            context={"is_active": user.is_active},
            **request_origin(request),
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

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin, require_owner
from app.models.role import Role
from app.models.user import User
from app.schemas.user import UserRead, UserRoleUpdate

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=list[UserRead])
def list_users(
    limit: int = Query(default=50, le=200, ge=1),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[UserRead]:
    return (
        db.query(User).order_by(User.created_at.desc()).limit(limit).offset(offset).all()
    )


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

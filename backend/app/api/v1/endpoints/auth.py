import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps_rate_limit import login_rate_limit, register_rate_limit
from app.core.security import TokenType, create_access_token, decode_token
from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    MessageResponse,
    RefreshRequest,
    TokenPair,
    VerifyEmailRequest,
)
from app.schemas.user import UserCreate, UserRead
from app.services.auth_service import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    authenticate_user,
    issue_token_pair,
    register_user,
)
from app.services.verification_service import send_verification_email, verify_email

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(register_rate_limit)],
)
def register(data: UserCreate, db: Session = Depends(get_db)) -> UserRead:
    try:
        user = register_user(db, data)
    except EmailAlreadyRegisteredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from exc

    send_verification_email(db, user)
    return user


@router.post("/verify-email", response_model=MessageResponse)
def verify_email_endpoint(
    data: VerifyEmailRequest, db: Session = Depends(get_db)
) -> MessageResponse:
    user = verify_email(db, data.token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification link is invalid or has expired",
        )
    return MessageResponse(detail="Email verified")


@router.post(
    "/resend-verification",
    response_model=MessageResponse,
    dependencies=[Depends(register_rate_limit)],
)
def resend_verification(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> MessageResponse:
    send_verification_email(db, current_user)
    # Same response whether or not an email went out, so this can't be used
    # to probe which accounts are already verified.
    return MessageResponse(detail="If the address needs verification, an email was sent")


@router.post(
    "/login", response_model=TokenPair, dependencies=[Depends(login_rate_limit)]
)
def login(data: LoginRequest, db: Session = Depends(get_db)) -> TokenPair:
    try:
        user = authenticate_user(db, data.email, data.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password"
        ) from exc
    return issue_token_pair(user)


@router.post("/refresh", response_model=TokenPair)
def refresh(data: RefreshRequest, db: Session = Depends(get_db)) -> TokenPair:
    payload = decode_token(data.refresh_token)
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
    )
    if payload is None or payload.get("type") != TokenType.REFRESH.value:
        raise invalid

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise invalid from exc

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise invalid

    return TokenPair(
        access_token=create_access_token(str(user.id)),
        refresh_token=data.refresh_token,
    )

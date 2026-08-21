import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps_rate_limit import (
    login_rate_limit,
    password_reset_rate_limit,
    register_rate_limit,
)
from app.api.request_info import client_ip, user_agent
from app.core.security import (
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.database import get_db
from app.deps import get_current_user
from app.models.usage_event import EventName
from app.models.user import User
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RefreshRequest,
    ResetPasswordRequest,
    TokenPair,
    VerifyEmailRequest,
)
from app.schemas.user import UserCreate, UserRead
from app.services.analytics_service import record_event
from app.services.auth_service import (
    EmailAlreadyRegisteredError,
    InactiveAccountError,
    InvalidCredentialsError,
    authenticate_user,
    issue_token_pair,
    register_user,
)
from app.services.password_reset_service import request_password_reset, reset_password
from app.services.session_service import authenticate as authenticate_session
from app.services.session_service import revoke_session, rotate, start_session
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
    record_event(db, EventName.USER_REGISTERED, user=user)
    return user


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    dependencies=[Depends(password_reset_rate_limit)],
)
def forgot_password(
    data: ForgotPasswordRequest, db: Session = Depends(get_db)
) -> MessageResponse:
    request_password_reset(db, data.email)
    # Deliberately identical whether or not the address exists: a differing
    # response would turn this into an account-enumeration oracle.
    return MessageResponse(
        detail="If that address has an account, a reset link has been sent"
    )


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    dependencies=[Depends(password_reset_rate_limit)],
)
def reset_password_endpoint(
    data: ResetPasswordRequest, db: Session = Depends(get_db)
) -> MessageResponse:
    user = reset_password(db, data.token, data.new_password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset link is invalid or has expired",
        )
    return MessageResponse(detail="Password updated")


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
    record_event(db, EventName.USER_VERIFIED, user=user)
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
def login(
    data: LoginRequest, request: Request, db: Session = Depends(get_db)
) -> TokenPair:
    try:
        user = authenticate_user(db, data.email, data.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password"
        ) from exc
    except InactiveAccountError as exc:
        # Distinct from a bad password: the caller proved who they are, and
        # telling them the account is disabled is what lets them ask an
        # admin to re-enable it instead of resetting a working password.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated",
        ) from exc

    # Only successful logins are recorded. Failures are a security signal,
    # not a product one, and they already go to the rate limiter and the log.
    record_event(db, EventName.USER_LOGGED_IN, user=user)

    tokens = issue_token_pair(user)
    # The refresh token only works while this row says it may. Recording it
    # here is what makes the session revocable, and what puts the device in
    # the owner's session list.
    start_session(
        db,
        user,
        tokens.refresh_token,
        user_agent=user_agent(request),
        ip_address=client_ip(request),
    )
    return tokens


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

    # A valid signature is no longer enough. The token must also still have a
    # session behind it — not revoked, not expired, not already rotated away —
    # which is what makes signing out and password reset able to stop it.
    session = authenticate_session(db, data.refresh_token)
    if session is None:
        raise invalid

    # Rotate: the presented token is retired here and the caller leaves with
    # a new one. A token captured in transit therefore stops working as soon
    # as the legitimate client next refreshes, rather than lasting weeks.
    refresh_token = create_refresh_token(str(user.id))
    rotate(db, session, refresh_token)

    return TokenPair(
        access_token=create_access_token(str(user.id)),
        refresh_token=refresh_token,
    )


@router.post("/logout", response_model=MessageResponse)
def logout(data: RefreshRequest, db: Session = Depends(get_db)) -> MessageResponse:
    """End the session behind this refresh token.

    Takes the refresh token rather than requiring an access token, because
    the refresh token is the thing being revoked and it is what a client
    still holds when its access token has already lapsed — signing out
    should not be the one action that needs a fresh login first.

    Always reports success. An unknown or already-revoked token is not an
    error the caller can act on, and answering differently would let someone
    test captured tokens for validity against an unauthenticated endpoint.
    """
    session = authenticate_session(db, data.refresh_token)
    if session is not None:
        user = db.get(User, session.user_id)
        if user is not None:
            revoke_session(db, user, session.id)

    return MessageResponse(detail="Signed out")

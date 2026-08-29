import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps_rate_limit import (
    login_rate_limit,
    password_reset_rate_limit,
    register_rate_limit,
)
from app.api.request_info import client_ip, request_origin, user_agent
from app.core.security import (
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.database import get_db
from app.deps import get_current_user
from app.models.audit_log import AuditAction, AuditOutcome
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
from app.services.audit_service import record as record_audit
from app.services.auth_service import (
    EmailAlreadyRegisteredError,
    InactiveAccountError,
    InvalidCredentialsError,
    authenticate_user,
    issue_token_pair,
    register_user,
)
from app.services.entitlement_service import entitlements_for
from app.services.password_reset_service import request_password_reset, reset_password
from app.services.session_service import authenticate as authenticate_session
from app.services.session_service import revoke_session, rotate, start_session
from app.services.verification_service import send_verification_email, verify_email

router = APIRouter(prefix="/auth", tags=["auth"])


def _audit_failed_login(
    db: Session, email: str, action: AuditAction, origin: dict[str, str | None]
) -> None:
    """Record a refused sign-in, attributing it only if the account exists.

    The lookup costs one query, and only on the failure path. It buys the
    row an owner actually needs — a refusal against *their* account — which
    an unattributed row could never be, since nothing would connect it to
    them without storing the address that was tried.
    """
    subject = db.query(User).filter(User.email == email).first()
    record_audit(
        db,
        action,
        user=subject,
        outcome=AuditOutcome.FAILURE,
        context={"known_account": subject is not None},
        **origin,
    )



@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(register_rate_limit)],
)
def register(
    data: UserCreate, request: Request, db: Session = Depends(get_db)
) -> UserRead:
    try:
        user = register_user(db, data)
    except EmailAlreadyRegisteredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from exc

    send_verification_email(db, user)
    record_event(db, EventName.USER_REGISTERED, user=user)
    # The first row in the account's trail, and the one that dates every
    # other. An owner reading their history should not have to infer when
    # the account started from the oldest sign-in they can still see.
    record_audit(
        db, AuditAction.ACCOUNT_REGISTERED, user=user, **request_origin(request)
    )
    return user


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    dependencies=[Depends(password_reset_rate_limit)],
)
def forgot_password(
    data: ForgotPasswordRequest, request: Request, db: Session = Depends(get_db)
) -> MessageResponse:
    request_password_reset(db, data.email)

    # Recorded against the account when the address is one we hold, and
    # anonymously when it is not. The row an owner needs to see is "somebody
    # asked to reset my password from an IP I don't recognise", which only
    # exists if the request is attributed; the unattributed rows are what
    # show a sweep across addresses that were never registered.
    subject = db.query(User).filter(User.email == data.email).first()
    record_audit(
        db,
        AuditAction.PASSWORD_RESET_REQUESTED,
        user=subject,
        context={"known_account": subject is not None},
        **request_origin(request),
    )
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
    data: ResetPasswordRequest, request: Request, db: Session = Depends(get_db)
) -> MessageResponse:
    user = reset_password(db, data.token, data.new_password)
    if user is None:
        # Not audited. There is no account to attribute it to — a token that
        # did not resolve names nobody — and a row saying only "an unknown
        # token was tried" is noise in every trail that would carry it.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset link is invalid or has expired",
        )

    # A completed reset ends every session, so this row is also the
    # explanation for the sign-outs the owner is about to notice.
    record_audit(
        db,
        AuditAction.PASSWORD_RESET_COMPLETED,
        user=user,
        **request_origin(request),
    )
    return MessageResponse(detail="Password updated")


@router.post("/verify-email", response_model=MessageResponse)
def verify_email_endpoint(
    data: VerifyEmailRequest, request: Request, db: Session = Depends(get_db)
) -> MessageResponse:
    user = verify_email(db, data.token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification link is invalid or has expired",
        )
    record_event(db, EventName.USER_VERIFIED, user=user)
    record_audit(
        db, AuditAction.EMAIL_VERIFIED, user=user, **request_origin(request)
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
def login(
    data: LoginRequest, request: Request, db: Session = Depends(get_db)
) -> TokenPair:
    origin = request_origin(request)
    try:
        user = authenticate_user(db, data.email, data.password)
    except InvalidCredentialsError as exc:
        # Attributed to the account when the address is one we hold, so the
        # owner sees "three failed sign-ins from an IP I don't recognise" in
        # their own trail. The address itself is never stored: an attempt
        # against an address nobody registered leaves an unattributed row,
        # which is what shows a spray without recording what was typed.
        _audit_failed_login(db, data.email, AuditAction.LOGIN_FAILED, origin)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password"
        ) from exc
    except InactiveAccountError as exc:
        # Distinct from a bad password: the caller proved who they are, and
        # telling them the account is disabled is what lets them ask an
        # admin to re-enable it instead of resetting a working password.
        # Distinct in the trail too — somebody signing in with the right
        # password to a switched-off account is a different story from
        # somebody guessing, and an admin looking at a deactivation wants to
        # know whether the owner has been trying to get back in.
        _audit_failed_login(db, data.email, AuditAction.LOGIN_BLOCKED, origin)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated",
        ) from exc

    # Only successful logins are recorded here. Failures are a security
    # signal rather than a product one, so they go to the audit trail above
    # instead — along with the rate limiter and the log, which is all they
    # had before that table existed.
    record_event(db, EventName.USER_LOGGED_IN, user=user)
    record_audit(db, AuditAction.LOGIN, user=user, **origin)

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
        # How many devices this account's plan keeps signed in at once.
        # Resolved here rather than inside the service so the session layer
        # stays unaware of billing: it takes a number, not a subscription.
        max_active_sessions=entitlements_for(user).max_active_sessions,
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
def logout(
    data: RefreshRequest, request: Request, db: Session = Depends(get_db)
) -> MessageResponse:
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
            # Only the real sign-out is recorded. An unknown token produces
            # no row on purpose: it names no account, and letting one write
            # a row would hand an unauthenticated caller a way to append to
            # the trail.
            record_audit(
                db,
                AuditAction.LOGOUT,
                user=user,
                context={"session_id": str(session.id)},
                **request_origin(request),
            )

    return MessageResponse(detail="Signed out")

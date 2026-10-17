from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.auth import TokenPair
from app.schemas.user import UserCreate


class EmailAlreadyRegisteredError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class InactiveAccountError(Exception):
    """The credentials were right, but the account is switched off."""


def register_user(db: Session, data: UserCreate) -> User:
    existing = db.query(User).filter(User.email == data.email).first()
    if existing is not None:
        raise EmailAlreadyRegisteredError(data.email)

    user = User(
        email=data.email,
        hashed_password=hash_password(data.password),
        full_name=data.full_name,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        # Two registrations for the same address landed between the lookup
        # above and this commit — the other one won the unique constraint.
        # Without this, the loser surfaces as an unhandled 500 instead of
        # the same 409 the lookup above would have given it a moment later.
        db.rollback()
        raise EmailAlreadyRegisteredError(data.email) from exc
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User:
    """Verify credentials, or raise.

    Deactivation is checked here and not only in get_current_user: issuing a
    token pair for a switched-off account hands out a refresh token that
    outlives the deactivation by up to REFRESH_TOKEN_EXPIRE_DAYS, and every
    other entry point already refuses inactive users.
    """
    user = db.query(User).filter(User.email == email).first()
    if user is None or not verify_password(password, user.hashed_password):
        raise InvalidCredentialsError()

    if not user.is_active:
        raise InactiveAccountError()

    return user


def issue_token_pair(user: User) -> TokenPair:
    subject = str(user.id)
    return TokenPair(
        access_token=create_access_token(subject),
        refresh_token=create_refresh_token(subject),
    )

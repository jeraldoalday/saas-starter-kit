import logging

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password
from app.models.token import OneTimeToken, TokenPurpose
from app.models.user import User
from app.services.email.factory import get_email_sender
from app.services.email.templates import password_reset_email
from app.services.session_service import revoke_all
from app.services.token_service import consume_token, issue_token

logger = logging.getLogger("app.password_reset")
settings = get_settings()


def request_password_reset(db: Session, email: str) -> None:
    """Email a reset link if the address belongs to an active account.

    Returns nothing either way: callers must respond identically whether or
    not the address exists, so this endpoint can't enumerate accounts.
    """
    user = db.query(User).filter(User.email == email).first()
    if user is None or not user.is_active:
        logger.info("password reset requested for unknown or inactive address")
        return

    raw_token = issue_token(db, user, TokenPurpose.PASSWORD_RESET)
    base = settings.frontend_base_url.rstrip("/")
    reset_url = f"{base}/reset-password?token={raw_token}"

    get_email_sender().send(password_reset_email(user.email, reset_url))
    logger.info("password reset email issued", extra={"user_id": str(user.id)})


def reset_password(db: Session, raw_token: str, new_password: str) -> User | None:
    """Redeem a reset token and set the new password.

    Returns None if the token was missing, expired or already spent.
    """
    token = consume_token(db, raw_token, TokenPurpose.PASSWORD_RESET)
    if token is None:
        return None

    user = db.get(User, token.user_id)
    if user is None or not user.is_active:
        return None

    user.hashed_password = hash_password(new_password)

    # Any other outstanding reset token is now stale: if the reset was
    # triggered by someone who shouldn't have access, their link dies too.
    _invalidate_other_reset_tokens(db, user, keep_id=token.id)

    db.commit()

    # And every signed-in device goes with it. Changing the password is the
    # one action people take *because* they believe someone else has access,
    # and until refresh tokens were revocable it did nothing to them: an
    # attacker holding one kept minting access tokens for the rest of its
    # lifetime, through the reset and well past it. Deliberately revokes all
    # sessions rather than sparing any, since the legitimate owner is not
    # signed in here — they are holding a reset link — and whoever is signed
    # in is exactly who this is meant to evict.
    revoked = revoke_all(db, user)

    logger.info(
        "password reset completed",
        extra={"user_id": str(user.id), "sessions_revoked": revoked},
    )
    return user


def _invalidate_other_reset_tokens(db: Session, user: User, keep_id) -> None:
    from datetime import UTC, datetime

    outstanding = (
        db.query(OneTimeToken)
        .filter(
            OneTimeToken.user_id == user.id,
            OneTimeToken.purpose == TokenPurpose.PASSWORD_RESET.value,
            OneTimeToken.used_at.is_(None),
            OneTimeToken.id != keep_id,
        )
        .all()
    )
    now = datetime.now(UTC)
    for stale in outstanding:
        stale.used_at = now

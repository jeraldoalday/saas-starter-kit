import logging

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.token import TokenPurpose
from app.models.user import User
from app.services.email.factory import get_email_sender
from app.services.email.templates import verification_email
from app.services.token_service import consume_token, issue_token

logger = logging.getLogger("app.verification")
settings = get_settings()


def send_verification_email(db: Session, user: User) -> None:
    """Issue a verification token and email the link.

    Already-verified users are a no-op so a stale tab can't spam them.
    """
    if user.is_verified:
        return

    raw_token = issue_token(db, user, TokenPurpose.EMAIL_VERIFICATION)
    base = settings.frontend_base_url.rstrip("/")
    verify_url = f"{base}/verify-email?token={raw_token}"

    get_email_sender().send(verification_email(user.email, verify_url))
    logger.info("verification email issued", extra={"user_id": str(user.id)})


def verify_email(db: Session, raw_token: str) -> User | None:
    """Redeem a verification token. Returns the user, or None if invalid."""
    token = consume_token(db, raw_token, TokenPurpose.EMAIL_VERIFICATION)
    if token is None:
        return None

    user = db.get(User, token.user_id)
    if user is None:
        return None

    user.is_verified = True
    db.commit()
    logger.info("email verified", extra={"user_id": str(user.id)})
    return user

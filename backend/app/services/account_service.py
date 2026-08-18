"""Account lifecycle: export what we hold, and delete it on request.

Both operations exist to answer data-subject requests (GDPR access and
erasure). Keep them in step with the schema: a new table holding personal
data needs a matching branch here, or the export quietly under-reports.
"""

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.models.subscription import Subscription
from app.models.token import OneTimeToken
from app.models.usage_event import UsageEvent
from app.models.user import User

logger = logging.getLogger("app.account")


class InvalidPasswordError(Exception):
    pass


def export_user_data(db: Session, user: User) -> dict[str, Any]:
    """Everything we hold about this user, as JSON-serialisable data.

    Password hashes and token hashes are deliberately excluded: they are
    credentials, not user data, and exporting them would only widen the
    blast radius of a leaked export file.
    """
    subscription = user.subscription
    token_count = (
        db.query(OneTimeToken).filter(OneTimeToken.user_id == user.id).count()
    )
    events = (
        db.query(UsageEvent)
        .filter(UsageEvent.user_id == user.id)
        .order_by(UsageEvent.occurred_at.desc())
        .all()
    )

    return {
        "account": {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "is_active": user.is_active,
            "is_verified": user.is_verified,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        },
        "subscription": (
            {
                "status": subscription.status,
                "stripe_customer_id": subscription.stripe_customer_id,
                "current_period_end": (
                    subscription.current_period_end.isoformat()
                    if subscription.current_period_end
                    else None
                ),
                "created_at": (
                    subscription.created_at.isoformat()
                    if subscription.created_at
                    else None
                ),
            }
            if subscription is not None
            else None
        ),
        "pending_email_tokens": token_count,
        # Behaviour recorded about someone is data held about them, so the
        # export has to include it or it under-reports.
        "usage_events": [
            {
                "name": event.name,
                "source": event.source,
                "properties": event.properties,
                "occurred_at": event.occurred_at.isoformat(),
            }
            for event in events
        ],
    }


def delete_account(db: Session, user: User, password: str) -> None:
    """Permanently delete the user after re-authenticating them.

    Requiring the password means a stolen access token alone cannot destroy
    an account. Subscriptions and tokens go via ON DELETE CASCADE.

    Note this does NOT cancel a live Stripe subscription - Stripe is the
    system of record for billing and deleting our row would otherwise leave
    the customer being charged. Cancel in Stripe first; see ROADMAP.
    """
    if not verify_password(password, user.hashed_password):
        raise InvalidPasswordError()

    user_id = user.id

    # Tokens and usage events have no ORM relationship on User, and SQLite
    # doesn't enforce foreign keys by default, so leaving these to ON DELETE
    # CASCADE would behave differently under test than in production.
    db.query(OneTimeToken).filter(OneTimeToken.user_id == user_id).delete(
        synchronize_session=False
    )
    db.query(UsageEvent).filter(UsageEvent.user_id == user_id).delete(
        synchronize_session=False
    )

    # The subscription goes via the relationship's delete-orphan cascade;
    # deleting it here too would issue a second DELETE for a gone row.
    db.delete(user)
    db.commit()
    logger.info("account deleted", extra={"user_id": str(user_id)})


def has_active_subscription(user: User) -> bool:
    subscription: Subscription | None = user.subscription
    return subscription is not None and subscription.status in ("active", "trialing")

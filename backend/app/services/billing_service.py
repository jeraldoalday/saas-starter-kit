from datetime import UTC, datetime

import stripe
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.user import User

settings = get_settings()
stripe.api_key = settings.stripe_secret_key


class InvalidWebhookSignatureError(Exception):
    pass


def _get_or_create_subscription(db: Session, user: User) -> Subscription:
    if user.subscription is not None:
        return user.subscription
    subscription = Subscription(user_id=user.id)
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    return subscription


def create_checkout_session(db: Session, user: User, success_url: str, cancel_url: str) -> str:
    """Create a Stripe Checkout session for the monthly plan and return its URL.

    Requires STRIPE_SECRET_KEY / STRIPE_PRICE_ID_MONTHLY to be configured; with
    placeholder test keys this will raise a stripe.error.AuthenticationError,
    which is expected until real Stripe credentials are wired in.
    """
    subscription = _get_or_create_subscription(db, user)

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=subscription.stripe_customer_id,
        customer_email=user.email if not subscription.stripe_customer_id else None,
        line_items=[{"price": settings.stripe_price_id_monthly, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=str(user.id),
    )
    return session.url


def handle_webhook_event(db: Session, payload: bytes, sig_header: str) -> None:
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        raise InvalidWebhookSignatureError() from exc

    data = event["data"]["object"]

    if event["type"] == "checkout.session.completed":
        _apply_checkout_completed(db, data)
    elif event["type"] in ("customer.subscription.updated", "customer.subscription.deleted"):
        _apply_subscription_update(db, data)


def _apply_checkout_completed(db: Session, session_obj: dict) -> None:
    user_id = session_obj.get("client_reference_id")
    if not user_id:
        return

    subscription = db.query(Subscription).filter(Subscription.user_id == user_id).first()
    if subscription is None:
        return

    subscription.stripe_customer_id = session_obj.get("customer")
    subscription.stripe_subscription_id = session_obj.get("subscription")
    subscription.status = SubscriptionStatus.ACTIVE.value
    db.commit()


def _apply_subscription_update(db: Session, subscription_obj: dict) -> None:
    stripe_subscription_id = subscription_obj.get("id")
    subscription = (
        db.query(Subscription)
        .filter(Subscription.stripe_subscription_id == stripe_subscription_id)
        .first()
    )
    if subscription is None:
        return

    subscription.status = subscription_obj.get("status", subscription.status)
    period_end = subscription_obj.get("current_period_end")
    if period_end:
        subscription.current_period_end = datetime.fromtimestamp(period_end, tz=UTC)
    db.commit()

import logging
import uuid
from datetime import UTC, datetime

import stripe
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.usage_event import EventName
from app.models.user import User
from app.models.webhook_event import ProcessedWebhookEvent
from app.services.analytics_service import record_event

logger = logging.getLogger("app.billing")
settings = get_settings()
stripe.api_key = settings.stripe_secret_key


class InvalidWebhookSignatureError(Exception):
    pass


class NoStripeCustomerError(Exception):
    """Raised when a billing action needs a Stripe customer the user doesn't have yet.

    A customer only exists after the user completes checkout at least once, so
    the portal is unreachable until then.
    """


def _get_or_create_subscription(db: Session, user: User) -> Subscription:
    if user.subscription is not None:
        return user.subscription
    subscription = Subscription(user_id=user.id)
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    return subscription


def create_checkout_session(
    db: Session,
    user: User,
    success_url: str | None = None,
    cancel_url: str | None = None,
) -> str:
    """Create a Stripe Checkout session for the monthly plan and return its URL.

    Redirect targets default to the configured frontend base URL, so the
    backend doesn't hardcode where the app is deployed.

    Requires STRIPE_SECRET_KEY / STRIPE_PRICE_ID_MONTHLY to be configured; with
    placeholder test keys this will raise a stripe.error.AuthenticationError,
    which is expected until real Stripe credentials are wired in.
    """
    subscription = _get_or_create_subscription(db, user)
    base = settings.frontend_base_url.rstrip("/")

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=subscription.stripe_customer_id,
        customer_email=user.email if not subscription.stripe_customer_id else None,
        line_items=[{"price": settings.stripe_price_id_monthly, "quantity": 1}],
        success_url=success_url or f"{base}/dashboard?checkout=success",
        cancel_url=cancel_url or f"{base}/dashboard?checkout=canceled",
        client_reference_id=str(user.id),
    )
    return session.url


def create_portal_session(db: Session, user: User, return_url: str | None = None) -> str:
    """Create a Stripe billing-portal session so the user can manage their plan.

    The portal is where Stripe hosts payment-method updates, invoice history,
    plan changes and cancellation, so none of that needs building here.
    """
    subscription = user.subscription
    if subscription is None or not subscription.stripe_customer_id:
        raise NoStripeCustomerError()

    base = settings.frontend_base_url.rstrip("/")
    session = stripe.billing_portal.Session.create(
        customer=subscription.stripe_customer_id,
        return_url=return_url or f"{base}/dashboard",
    )
    return session.url


def handle_webhook_event(db: Session, payload: bytes, sig_header: str) -> None:
    """Verify a Stripe webhook's signature and apply it exactly once."""
    if not settings.stripe_webhook_secret:
        # Fail closed: without a secret every payload would be unverifiable,
        # so an unauthenticated caller could drive subscription state.
        logger.error("stripe webhook received but STRIPE_WEBHOOK_SECRET is unset")
        raise InvalidWebhookSignatureError()

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        logger.warning("stripe webhook signature rejected")
        raise InvalidWebhookSignatureError() from exc

    event_id = event.get("id")
    event_type = event.get("type", "")

    if event_id and _already_processed(db, event_id):
        logger.info(
            "stripe webhook replay ignored",
            extra={"stripe_event_id": event_id, "stripe_event_type": event_type},
        )
        return

    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        _apply_checkout_completed(db, data)
    elif event_type in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        _apply_subscription_update(db, data, deleted=event_type.endswith(".deleted"))
    elif event_type == "invoice.payment_failed":
        _apply_payment_failed(db, data)
    else:
        logger.info(
            "stripe webhook ignored", extra={"stripe_event_type": event_type}
        )

    if event_id:
        _mark_processed(db, event_id, event_type)

    logger.info(
        "stripe webhook applied",
        extra={"stripe_event_id": event_id, "stripe_event_type": event_type},
    )


def _already_processed(db: Session, event_id: str) -> bool:
    return db.get(ProcessedWebhookEvent, event_id) is not None


def _mark_processed(db: Session, event_id: str, event_type: str) -> None:
    db.add(ProcessedWebhookEvent(id=event_id, event_type=event_type))
    try:
        db.commit()
    except IntegrityError:
        # Concurrent delivery of the same event; the other writer won.
        db.rollback()


def _subscription_for_user_id(db: Session, raw_user_id: str) -> Subscription | None:
    try:
        user_id = uuid.UUID(raw_user_id)
    except (ValueError, AttributeError, TypeError):
        logger.warning("stripe webhook carried an unparseable client_reference_id")
        return None
    return db.query(Subscription).filter(Subscription.user_id == user_id).first()


def _apply_checkout_completed(db: Session, session_obj: dict) -> None:
    raw_user_id = session_obj.get("client_reference_id")
    if not raw_user_id:
        return

    subscription = _subscription_for_user_id(db, raw_user_id)
    if subscription is None:
        return

    subscription.stripe_customer_id = session_obj.get("customer")
    subscription.stripe_subscription_id = session_obj.get("subscription")
    subscription.status = SubscriptionStatus.ACTIVE.value
    db.commit()
    record_event(
        db,
        EventName.SUBSCRIPTION_ACTIVATED,
        user_id=subscription.user_id,
        properties={"source": "checkout"},
    )


def _apply_subscription_update(
    db: Session, subscription_obj: dict, deleted: bool = False
) -> None:
    stripe_subscription_id = subscription_obj.get("id")
    subscription = (
        db.query(Subscription)
        .filter(Subscription.stripe_subscription_id == stripe_subscription_id)
        .first()
    )
    if subscription is None:
        return

    previous_status = subscription.status

    if deleted:
        # A deletion event can still carry status="active" for the final
        # period, so the terminal state is set explicitly rather than copied.
        subscription.status = SubscriptionStatus.CANCELED.value
    else:
        subscription.status = subscription_obj.get("status", subscription.status)

    period_end = subscription_obj.get("current_period_end")
    if period_end:
        subscription.current_period_end = datetime.fromtimestamp(period_end, tz=UTC)
    db.commit()

    _record_status_change(db, subscription, previous_status)


def _apply_payment_failed(db: Session, invoice_obj: dict) -> None:
    stripe_subscription_id = invoice_obj.get("subscription")
    if not stripe_subscription_id:
        return

    subscription = (
        db.query(Subscription)
        .filter(Subscription.stripe_subscription_id == stripe_subscription_id)
        .first()
    )
    if subscription is None:
        return

    previous_status = subscription.status
    subscription.status = SubscriptionStatus.PAST_DUE.value
    db.commit()

    _record_status_change(db, subscription, previous_status)


_STATUS_EVENTS = {
    SubscriptionStatus.ACTIVE.value: EventName.SUBSCRIPTION_ACTIVATED,
    SubscriptionStatus.CANCELED.value: EventName.SUBSCRIPTION_CANCELED,
    SubscriptionStatus.PAST_DUE.value: EventName.SUBSCRIPTION_PAST_DUE,
}


def _record_status_change(
    db: Session, subscription: Subscription, previous_status: str
) -> None:
    """Emit an event only when the status actually moved.

    Stripe re-sends customer.subscription.updated for changes that don't
    touch status at all (a payment method, a trial end date). Recording
    unconditionally would turn those into phantom churn and activation
    spikes in the funnel.
    """
    if subscription.status == previous_status:
        return

    event_name = _STATUS_EVENTS.get(subscription.status)
    if event_name is None:
        return

    record_event(
        db,
        event_name,
        user_id=subscription.user_id,
        properties={"from": previous_status, "to": subscription.status},
    )

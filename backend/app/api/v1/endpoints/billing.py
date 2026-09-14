from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_api_or_session_user, get_verified_user
from app.models.usage_event import EventName
from app.models.user import User
from app.schemas.billing import (
    CheckoutSessionResponse,
    PortalSessionResponse,
    SubscriptionRead,
)
from app.services.analytics_service import record_event
from app.services.billing_service import (
    AlreadySubscribedError,
    InvalidWebhookSignatureError,
    NoStripeCustomerError,
    create_checkout_session,
    create_portal_session,
    handle_webhook_event,
)

router = APIRouter(prefix="/billing", tags=["billing"])


@router.post("/checkout", response_model=CheckoutSessionResponse)
def checkout(
    current_user: User = Depends(get_verified_user),
    db: Session = Depends(get_db),
) -> CheckoutSessionResponse:
    try:
        url = create_checkout_session(db, current_user)
    except AlreadySubscribedError as exc:
        # Stripe would open a second subscription for the same customer
        # rather than refusing, so this has to stop the request before it
        # gets there — manage the existing one from the portal instead.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have an active subscription — manage it from the billing portal",
        ) from exc

    # Recorded after Stripe accepted the session, so the funnel counts
    # intents that actually reached checkout rather than failed attempts.
    record_event(db, EventName.CHECKOUT_STARTED, user=current_user)
    return CheckoutSessionResponse(checkout_url=url)


@router.post("/portal", response_model=PortalSessionResponse)
def portal(
    current_user: User = Depends(get_verified_user),
    db: Session = Depends(get_db),
) -> PortalSessionResponse:
    try:
        url = create_portal_session(db, current_user)
    except NoStripeCustomerError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No billing account yet — complete checkout first",
        ) from exc

    record_event(db, EventName.PORTAL_OPENED, user=current_user)
    return PortalSessionResponse(portal_url=url)


@router.get("/subscription", response_model=SubscriptionRead | None)
def get_subscription(
    current_user: User = Depends(get_api_or_session_user),
) -> SubscriptionRead | None:
    return current_user.subscription


@router.post("/webhook", status_code=status.HTTP_204_NO_CONTENT)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)) -> None:
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        handle_webhook_event(db, payload, sig_header)
    except InvalidWebhookSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook signature"
        ) from exc

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.billing_plans import BILLING_PLANS
from app.core.entitlements import (
    ENTITLEMENTS_BY_PLAN,
    FREE_PLAN,
    entitlements_for_plan,
)
from app.database import get_db
from app.deps import get_api_or_session_user
from app.models.user import User
from app.schemas.entitlements import (
    EntitlementsRead,
    PlanCatalogEntry,
    PlanPrice,
    PlanRead,
    QuotaRead,
)
from app.services.entitlement_service import (
    next_period_start,
    plan_key_for,
    quota_snapshot,
)

# Reachable with an API key as well as a session, unlike the credential and
# audit routes: a client's own remaining allowance is exactly the thing a
# programmatic caller needs in order to back off before it starts being
# refused, and it reveals nothing a session could not already read.
router = APIRouter(prefix="/plan", tags=["plan"])


@router.get("", response_model=PlanRead)
def read_plan(
    current_user: User = Depends(get_api_or_session_user),
    db: Session = Depends(get_db),
) -> PlanRead:
    """The caller's plan and what is left of each of its ceilings.

    Three counting queries, which is why this is its own endpoint rather
    than fields bolted onto /users/me: a request that only wants to know who
    it is should not pay for a count of every event the account recorded
    this month.
    """
    snapshot = quota_snapshot(db, current_user)
    return PlanRead(
        entitlements=EntitlementsRead.model_validate(snapshot.entitlements),
        subscription_status=snapshot.subscription_status,
        quotas=[
            QuotaRead(
                key=quota.key,
                label=quota.label,
                limit=quota.limit,
                used=quota.used,
                remaining=quota.remaining,
                exhausted=quota.exhausted,
            )
            for quota in snapshot.quotas
        ],
        period_start=snapshot.period_start,
        period_end=next_period_start(),
    )


@router.get("/catalog", response_model=list[PlanCatalogEntry])
def read_catalog(
    current_user: User = Depends(get_api_or_session_user),
) -> list[PlanCatalogEntry]:
    """Every plan on offer, cheapest first, with the caller's marked.

    Built by joining two code-defined lists rather than by asking Stripe:
    the ceilings only exist here, and a pricing table that needs a network
    call to a third party is a pricing table that goes blank when the third
    party is having a bad day.

    A plan with entitlements but no price is served with `price: null` —
    the free tier, which is not a Stripe product. A plan with a price but no
    entitlements is not served at all, because there would be nothing to say
    about what it buys; tests/test_entitlements.py asserts that gap cannot
    exist.
    """
    current_plan_key = plan_key_for(current_user)

    entries = [
        PlanCatalogEntry(
            entitlements=EntitlementsRead.model_validate(FREE_PLAN),
            price=None,
            description="Everything you need to try the product out.",
            is_current=current_plan_key == FREE_PLAN.plan_key,
        )
    ]

    for plan in BILLING_PLANS:
        if plan.key not in ENTITLEMENTS_BY_PLAN:
            continue
        entries.append(
            PlanCatalogEntry(
                entitlements=EntitlementsRead.model_validate(
                    entitlements_for_plan(plan.key)
                ),
                price=PlanPrice(
                    unit_amount=plan.unit_amount,
                    currency=plan.currency,
                    interval=plan.interval,
                ),
                description=plan.product_description,
                is_current=current_plan_key == plan.key,
            )
        )

    # Cheapest first, free at the head. Sorted rather than assumed, so a
    # plan added out of order to BILLING_PLANS still lands where a reader
    # expects it.
    entries.sort(key=lambda entry: entry.price.unit_amount if entry.price else 0)
    return entries

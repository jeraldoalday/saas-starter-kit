"""Sync the code-defined billing catalog to a real Stripe account.

Products and prices are looked up by `lookup_key` before creating anything,
so running the sync repeatedly against the same Stripe account is
idempotent: existing plans are reused, only missing ones are created.
"""

from dataclasses import dataclass

import stripe

from app.core.billing_plans import BILLING_PLANS, BillingPlan
from app.core.config import get_settings

settings = get_settings()
stripe.api_key = settings.stripe_secret_key


@dataclass(frozen=True)
class SyncedPrice:
    plan_key: str
    product_id: str
    price_id: str
    created: bool


def _find_price_by_lookup_key(lookup_key: str) -> stripe.Price | None:
    result = stripe.Price.list(lookup_keys=[lookup_key], active=True, limit=1)
    return result.data[0] if result.data else None


def _sync_plan(plan: BillingPlan) -> SyncedPrice:
    existing = _find_price_by_lookup_key(plan.lookup_key)
    if existing is not None:
        return SyncedPrice(
            plan_key=plan.key,
            product_id=existing.product,
            price_id=existing.id,
            created=False,
        )

    product = stripe.Product.create(
        name=plan.product_name,
        description=plan.product_description,
        metadata={"plan_key": plan.key},
    )
    price = stripe.Price.create(
        product=product.id,
        currency=plan.currency,
        unit_amount=plan.unit_amount,
        recurring={"interval": plan.interval},
        lookup_key=plan.lookup_key,
    )
    return SyncedPrice(plan_key=plan.key, product_id=product.id, price_id=price.id, created=True)


def sync_catalog(plans: list[BillingPlan] | None = None) -> list[SyncedPrice]:
    """Create (or reuse) the Stripe product/price for each configured plan."""
    return [_sync_plan(plan) for plan in (BILLING_PLANS if plans is None else plans)]


def get_price_id(lookup_key: str) -> str | None:
    """Resolve a plan's live Stripe price ID by its lookup key."""
    price = _find_price_by_lookup_key(lookup_key)
    return price.id if price else None

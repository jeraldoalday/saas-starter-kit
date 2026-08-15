"""Code-defined billing catalog.

Plans live here instead of being clicked together in the Stripe dashboard,
so the product/price catalog is versioned, reviewable, and reproducible
across Stripe accounts (e.g. test vs. live). `app.services.stripe_catalog`
syncs this list to whichever Stripe account STRIPE_SECRET_KEY points at.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BillingPlan:
    key: str
    product_name: str
    product_description: str
    lookup_key: str
    unit_amount: int
    currency: str
    interval: str


BILLING_PLANS: list[BillingPlan] = [
    BillingPlan(
        key="pro_monthly",
        product_name="Pro",
        product_description="Full platform access, billed monthly.",
        lookup_key="pro_monthly",
        unit_amount=2900,
        currency="usd",
        interval="month",
    ),
]

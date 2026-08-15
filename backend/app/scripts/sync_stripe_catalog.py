"""CLI: sync the billing catalog (app.core.billing_plans) to Stripe.

Run with real credentials in STRIPE_SECRET_KEY (test or live) to create or
reuse the Product/Price objects the plans describe:

    python -m app.scripts.sync_stripe_catalog
"""

import sys

from app.core.config import get_settings
from app.services.stripe_catalog import sync_catalog


def main() -> int:
    settings = get_settings()
    if not settings.stripe_secret_key:
        print("STRIPE_SECRET_KEY is not set; nothing to sync.", file=sys.stderr)
        return 1

    for result in sync_catalog():
        action = "created" if result.created else "reused existing"
        print(f"{result.plan_key}: {action} price {result.price_id} (product {result.product_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

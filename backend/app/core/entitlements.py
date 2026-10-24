"""What a plan actually entitles an account to.

`app.core.billing_plans` says what a plan *costs*. This says what it *buys*,
and the split is deliberate: the price catalog is synced to Stripe, so
anything living there has to be a concept Stripe understands. A ceiling on
API keys is not, and putting it in Stripe metadata would mean the answer to
"how many keys may this account hold?" depends on a network call to a third
party during a request that is otherwise entirely local.

So limits are code, versioned alongside the endpoints that enforce them. The
rules that follow from that:

1. **Every plan names every limit.** No inheritance, no partial overrides. A
   plan added later that forgets a field is a plan with an accidental
   ceiling, and the failure is invisible until a customer hits it.
2. **`None` means no ceiling**, not zero and not "unset". It serialises to
   JSON null, which a client can render as "Unlimited" without a sentinel
   number to recognise.
3. **A limit here is a product promise, not a safety bound.** The services
   keep their own hard ceilings (`MAX_ACTIVE_KEYS_PER_USER`,
   `MAX_ACTIVE_SESSIONS_PER_USER`, `MAX_ENDPOINTS_PER_USER`) and those still
   apply: an unlimited plan is unlimited up to what the deployment can
   carry. tests/test_entitlements asserts no plan promises more than the
   backstop allows.
"""

from dataclasses import dataclass, fields

# The plan an account is on when it has never paid — no subscription row at
# all, or one that is canceled or past due. Not a Stripe product: nothing is
# ever charged for it, so there is nothing to sync.
FREE_PLAN_KEY = "free"


@dataclass(frozen=True)
class Entitlements:
    """The ceilings one plan grants.

    Frozen because this is a catalog entry, not per-account state: two
    accounts on the same plan share one instance, and a caller that could
    mutate it would be changing the plan for everybody in the process.
    """

    plan_key: str
    display_name: str

    # Credentials that can reach the API without a browser. The free tier
    # gets enough for one integration and a spare, which is the shape of
    # "trying it out"; a real deployment wiring up CI, a Zapier hook and a
    # staging environment is a paying deployment.
    max_active_api_keys: int | None

    # Signed-in devices. Reaching this does not refuse a login — the session
    # service retires the oldest instead, for the reason it documents: a
    # correct password must not fail because of housekeeping.
    max_active_sessions: int | None

    # Product events accepted per calendar month, counted from the first of
    # the month in UTC. Monthly rather than rolling because that is the
    # window a customer can reconcile against an invoice.
    monthly_event_allowance: int | None

    # URLs the account may have us POST to when something happens. Priced
    # lower than API keys and deliberately so: an endpoint is fan-out we pay
    # for, not a credential the customer holds. One is enough to wire a
    # production receiver up, which is what the free tier is for; separating
    # staging from production, or fanning out to a second team's service, is
    # a shape that belongs to a deployment with a budget.
    max_webhook_endpoints: int | None

    # How far back the account can read its own security trail. The rows are
    # kept for AUDIT_RETENTION_DAYS regardless — this bounds reading, not
    # recording, so upgrading makes history that already exists visible
    # rather than starting the clock over.
    audit_history_days: int | None


#: Named so the free tier can be referred to without a dictionary lookup,
#: which is what the fallback path in `entitlements_for_plan` needs.
FREE_PLAN = Entitlements(
    plan_key=FREE_PLAN_KEY,
    display_name="Free",
    max_active_api_keys=2,
    max_active_sessions=3,
    max_webhook_endpoints=1,
    monthly_event_allowance=10_000,
    audit_history_days=30,
)

PRO_PLAN = Entitlements(
    plan_key="pro_monthly",
    display_name="Pro",
    max_active_api_keys=20,
    max_active_sessions=10,
    max_webhook_endpoints=5,
    monthly_event_allowance=1_000_000,
    audit_history_days=180,
)

#: Every plan an account can be on, keyed the way `BillingPlan.key` spells
#: it — so a subscription's plan and its entitlements cannot drift apart
#: without a test noticing.
ENTITLEMENTS_BY_PLAN: dict[str, Entitlements] = {
    plan.plan_key: plan for plan in (FREE_PLAN, PRO_PLAN)
}

#: The fields that are ceilings, as opposed to the two that are labels.
#: Iterated by the quota snapshot and by the tests that check every plan
#: names every limit, so adding a field here is enough to have it enforced
#: consistently rather than needing three lists updated in step.
LIMIT_FIELDS: tuple[str, ...] = tuple(
    field.name
    for field in fields(Entitlements)
    if field.name not in ("plan_key", "display_name")
)


def entitlements_for_plan(plan_key: str | None) -> Entitlements:
    """The ceilings for `plan_key`, falling back to the free tier.

    Falls back rather than raising, and that direction is the safe one: an
    unknown plan key means a subscription referencing a product this build
    does not know about — a rolled-back deploy, or a price created straight
    in the Stripe dashboard. Treating it as free under-serves one customer
    until someone notices; treating it as unlimited hands every ceiling in
    the product to whoever can get a string into that column.
    """
    if plan_key is None:
        return FREE_PLAN
    return ENTITLEMENTS_BY_PLAN.get(plan_key, FREE_PLAN)


def limit_for(entitlements: Entitlements, limit_name: str) -> int | None:
    """One named ceiling, or None if the plan does not cap it.

    A lookup by name rather than attribute access so the snapshot can walk
    LIMIT_FIELDS without a branch per limit.
    """
    if limit_name not in LIMIT_FIELDS:
        raise KeyError(f"{limit_name!r} is not an entitlement limit")
    return getattr(entitlements, limit_name)


def remaining(limit: int | None, used: int) -> int | None:
    """How much of `limit` is left, or None when there is no ceiling.

    Clamped at zero: a limit lowered by a downgrade can leave an account
    already over it, and "-3 keys remaining" is a number no interface has a
    sensible way to render.
    """
    if limit is None:
        return None
    return max(limit - used, 0)


def is_exhausted(limit: int | None, used: int) -> bool:
    """Whether `used` has reached `limit`. Never true without a ceiling."""
    return limit is not None and used >= limit


__all__ = [
    "ENTITLEMENTS_BY_PLAN",
    "FREE_PLAN",
    "FREE_PLAN_KEY",
    "LIMIT_FIELDS",
    "PRO_PLAN",
    "Entitlements",
    "entitlements_for_plan",
    "is_exhausted",
    "limit_for",
    "remaining",
]

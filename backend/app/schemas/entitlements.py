"""Wire shapes for plans, their ceilings, and how much is left of them."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class EntitlementsRead(BaseModel):
    """One plan's ceilings, exactly as the catalog defines them.

    Every limit is nullable, and null means *no ceiling* rather than
    "unknown" or zero. A client renders it as "Unlimited"; there is no
    sentinel number to recognise, which is the point of the shape.
    """

    model_config = ConfigDict(from_attributes=True)

    plan_key: str
    display_name: str
    max_active_api_keys: int | None
    max_active_sessions: int | None
    monthly_event_allowance: int | None
    audit_history_days: int | None


class QuotaRead(BaseModel):
    """One ceiling and how much of it this account has spent.

    `remaining` is served rather than left to the client to subtract, so
    every interface agrees about what happens when a downgrade leaves an
    account over its new limit — it clamps at zero rather than going
    negative in one place and not another.
    """

    key: str = Field(description='Stable identifier: "api_keys", "sessions", "events".')
    label: str = Field(description="Human wording for the thing being counted.")
    limit: int | None
    used: int
    remaining: int | None
    exhausted: bool


class PlanRead(BaseModel):
    """The caller's plan, its ceilings, and what is left of each."""

    entitlements: EntitlementsRead
    subscription_status: str | None = Field(
        default=None,
        description=(
            "The Stripe subscription status behind the plan, or null when "
            "the account has never subscribed. Reported because \"Free "
            "because you never subscribed\" and \"Free because your card was "
            "declined\" want different words in the interface."
        ),
    )
    quotas: list[QuotaRead]
    period_start: datetime = Field(
        description="When the monthly event allowance last reset (UTC)."
    )
    period_end: datetime = Field(
        description="When it next resets (UTC). Exclusive: the first moment "
        "of the following month."
    )


class PlanPrice(BaseModel):
    """What a plan costs, for the plans that cost anything."""

    unit_amount: int = Field(description="In the currency's smallest unit.")
    currency: str
    interval: str


class PlanCatalogEntry(BaseModel):
    """One plan on offer: what it buys, what it costs, whether you are on it.

    Price is null for the free tier, which is not a Stripe product — there
    is nothing to charge, so there is nothing to sync. That makes null the
    honest answer rather than a zero that implies a £0.00 line item.
    """

    entitlements: EntitlementsRead
    price: PlanPrice | None = None
    description: str | None = None
    is_current: bool = Field(
        description="Whether this is the plan the caller is on right now."
    )

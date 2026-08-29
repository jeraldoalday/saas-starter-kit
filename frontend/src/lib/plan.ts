import { apiRequest } from "@/lib/api-client";
import { getAccessToken } from "@/lib/auth";
import type { Plan, PlanCatalogEntry, PlanPrice, Quota } from "@/lib/types";

/**
 * The account's plan, its ceilings, and how much of each is left.
 *
 * Everything here is presentation over numbers the API already computed.
 * In particular `remaining` is never recalculated on this side: a downgrade
 * can leave an account over its new limit, and the server clamps that at
 * zero — subtracting again here is how one view ends up showing "-3 keys
 * remaining" while another shows none.
 */

/** Fraction of a quota spent, above which the meter starts warning. */
export const WARNING_THRESHOLD = 0.8;

export function fetchPlan(): Promise<Plan> {
  return apiRequest<Plan>("/plan", { token: getAccessToken() });
}

export function fetchPlanCatalog(): Promise<PlanCatalogEntry[]> {
  return apiRequest<PlanCatalogEntry[]>("/plan/catalog", {
    token: getAccessToken(),
  });
}

/** One named quota out of a plan, or null if this build does not know it. */
export function findQuota(plan: Plan, key: string): Quota | null {
  return plan.quotas.find((quota) => quota.key === key) ?? null;
}

/**
 * A ceiling as a person reads it.
 *
 * Null is the API saying "no ceiling", which is a promise rather than
 * missing data — so it renders as a word, not as an em dash.
 */
export function formatLimit(limit: number | null): string {
  return limit === null ? "Unlimited" : limit.toLocaleString();
}

/** "3 of 20", or "3 used" where there is nothing to be out of. */
export function formatUsage(quota: Quota): string {
  if (quota.limit === null) return `${quota.used.toLocaleString()} used`;
  return `${quota.used.toLocaleString()} of ${quota.limit.toLocaleString()}`;
}

/**
 * How full a quota is, 0–1, for a meter.
 *
 * Clamped at both ends. An unlimited quota reports 0 rather than something
 * proportional to nothing, and an account left over its ceiling by a
 * downgrade reports a full bar rather than one that overflows its track.
 */
export function quotaFraction(quota: Quota): number {
  if (quota.limit === null || quota.limit <= 0) return 0;
  return Math.min(Math.max(quota.used / quota.limit, 0), 1);
}

export type QuotaTone = "normal" | "warning" | "exhausted";

/**
 * How much attention a quota deserves.
 *
 * Three levels rather than a boolean, for the reason the audit trail gives
 * about its own tones: a bar that turns red only once everything has
 * already stopped working has told the reader nothing they could have acted
 * on. The warning band is what makes the meter useful.
 */
export function quotaTone(quota: Quota): QuotaTone {
  if (quota.exhausted) return "exhausted";
  if (quotaFraction(quota) >= WARNING_THRESHOLD) return "warning";
  return "normal";
}

/** Quotas worth surfacing before anything breaks, fullest first. */
export function pressuredQuotas(plan: Plan): Quota[] {
  return plan.quotas
    .filter((quota) => quotaTone(quota) !== "normal")
    .sort((left, right) => quotaFraction(right) - quotaFraction(left));
}

/**
 * A price as a person reads it: "$29 / month".
 *
 * Whole units where the amount divides evenly, because a pricing table full
 * of ".00" is noise. Falls back to the currency code for anything
 * Intl cannot format, rather than throwing inside a render.
 */
export function formatPrice(price: PlanPrice | null): string {
  if (price === null) return "Free";

  const amount = price.unit_amount / 100;
  try {
    const formatted = new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: price.currency.toUpperCase(),
      minimumFractionDigits: Number.isInteger(amount) ? 0 : 2,
    }).format(amount);
    return `${formatted} / ${price.interval}`;
  } catch {
    return `${amount} ${price.currency.toUpperCase()} / ${price.interval}`;
  }
}

/**
 * Why the account is on the plan it is on.
 *
 * The distinction the API goes out of its way to report: "Free because you
 * never subscribed" and "Free because your card was declined" are the same
 * plan and completely different situations, and only one of them is worth
 * interrupting somebody about.
 */
export function describePlanStatus(plan: Plan): string {
  const { display_name: name } = plan.entitlements;
  switch (plan.subscription_status) {
    case null:
      return `You are on the ${name} plan.`;
    case "trialing":
      return `You are trialling the ${name} plan.`;
    case "active":
      return `You are on the ${name} plan.`;
    case "past_due":
      return `Your payment failed, so you are back on the ${name} plan until it is settled.`;
    case "canceled":
      return `Your subscription was canceled, so you are on the ${name} plan.`;
    default:
      return `You are on the ${name} plan.`;
  }
}

/** Whether the plan is one the account is paying for right now. */
export function isPaidPlan(plan: Plan): boolean {
  return plan.subscription_status === "active" || plan.subscription_status === "trialing";
}

/**
 * When the monthly event allowance next resets, as a local date.
 *
 * Returns null for an unparseable timestamp rather than "Invalid Date":
 * the sentence it appears in is decoration, and a broken clock should cost
 * the sentence, not the panel.
 */
export function formatPeriodEnd(plan: Plan): string | null {
  const end = new Date(plan.period_end);
  return Number.isNaN(end.getTime()) ? null : end.toLocaleDateString();
}

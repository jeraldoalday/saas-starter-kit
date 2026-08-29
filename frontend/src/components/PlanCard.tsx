import { useCallback, useEffect, useState } from "react";

import { Card } from "@/components/ui/Card";
import { Meter } from "@/components/ui/Meter";
import {
  describePlanStatus,
  fetchPlan,
  formatLimit,
  formatPeriodEnd,
  formatUsage,
  isPaidPlan,
  pressuredQuotas,
  quotaFraction,
  quotaTone,
} from "@/lib/plan";
import type { Plan, Quota } from "@/lib/types";

function QuotaRow({ quota }: { quota: Quota }) {
  const tone = quotaTone(quota);

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between text-sm">
        <span className="text-neutral-300">{quota.label}</span>
        <span
          className={`tabular-nums ${
            tone === "exhausted"
              ? "text-red-400"
              : tone === "warning"
                ? "text-amber-400"
                : "text-neutral-400"
          }`}
        >
          {formatUsage(quota)}
        </span>
      </div>
      <Meter
        value={quotaFraction(quota)}
        tone={tone}
        label={quota.label}
        valueText={formatUsage(quota)}
      />
    </div>
  );
}

/**
 * What the account's plan buys and how much of it is spent.
 *
 * Deliberately separate from BillingCard, which is about the subscription
 * as a commercial object — status, renewal, the portal. This is about what
 * the product will let you do today, which is the question somebody has
 * when a key creation just failed, and they are not the same panel even
 * though both read from the same subscription.
 *
 * There is no upgrade button here on purpose. BillingCard already owns the
 * one that starts a checkout, and two buttons doing the same thing on one
 * screen is how a reader ends up unsure whether they mean different things.
 */
export function PlanCard() {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    try {
      setPlan(await fetchPlan());
      setFailed(false);
    } catch {
      // A plan panel that cannot load says so and leaves the rest of the
      // dashboard alone, exactly as the usage panel does.
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  const underPressure = plan ? pressuredQuotas(plan) : [];
  const resets = plan ? formatPeriodEnd(plan) : null;

  return (
    <Card>
      <div className="mb-4 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-neutral-100">Plan</h2>
        {plan && (
          <span
            className={`rounded px-2 py-0.5 text-xs ${
              isPaidPlan(plan)
                ? "bg-indigo-500/15 text-indigo-300"
                : "bg-neutral-800 text-neutral-400"
            }`}
          >
            {plan.entitlements.display_name}
          </span>
        )}
      </div>

      {loading && <p className="text-sm text-neutral-500">Loading…</p>}

      {!loading && failed && (
        <p className="text-sm text-neutral-500">Plan details are unavailable right now.</p>
      )}

      {!loading && !failed && plan && (
        <>
          <p className="mb-4 text-sm text-neutral-400">{describePlanStatus(plan)}</p>

          {underPressure.length > 0 && (
            <p className="mb-4 rounded border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-300">
              {underPressure[0].exhausted
                ? `You have used all of your ${underPressure[0].label}.`
                : `You are close to your limit on ${underPressure[0].label}.`}
            </p>
          )}

          <div className="flex flex-col gap-4">
            {plan.quotas.map((quota) => (
              <QuotaRow key={quota.key} quota={quota} />
            ))}
          </div>

          <dl className="mt-5 grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 border-t border-neutral-800 pt-4 text-sm text-neutral-500">
            <dt>Security history</dt>
            <dd className="tabular-nums text-neutral-400">
              {formatLimit(plan.entitlements.audit_history_days)}
              {plan.entitlements.audit_history_days === null ? "" : " days"}
            </dd>
            {resets && (
              <>
                <dt>Event allowance resets</dt>
                <dd className="tabular-nums text-neutral-400">{resets}</dd>
              </>
            )}
          </dl>
        </>
      )}
    </Card>
  );
}

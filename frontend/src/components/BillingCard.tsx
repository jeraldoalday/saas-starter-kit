import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ApiClientError } from "@/lib/api-client";
import {
  fetchSubscription,
  isSubscriptionActive,
  openBillingPortal,
  startCheckout,
} from "@/lib/billing";
import type { Subscription } from "@/lib/types";

function formatRenewal(subscription: Subscription): string | null {
  if (!subscription.current_period_end) return null;
  const date = new Date(subscription.current_period_end);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString();
}

export function BillingCard() {
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [loading, setLoading] = useState(true);
  // Distinct from "no subscription": a failed fetch tells us nothing about
  // billing status, where a successful one returning null is a confirmed
  // answer. Conflating the two would offer a "Subscribe" button to someone
  // who is already paying, just because their status failed to load.
  const [failed, setFailed] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setSubscription(await fetchSubscription());
      setFailed(false);
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  async function redirectTo(action: () => Promise<string>) {
    setPending(true);
    setError(null);
    try {
      window.location.href = await action();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Something went wrong");
      setPending(false);
    }
  }

  const active = isSubscriptionActive(subscription);
  const renewal = subscription ? formatRenewal(subscription) : null;

  return (
    <Card>
      <h2 className="mb-2 text-sm font-semibold text-neutral-100">Subscription</h2>

      {loading ? (
        <p className="text-sm text-neutral-500">Loading…</p>
      ) : failed ? (
        <p className="text-sm text-neutral-500">
          Could not load your subscription status. Reload the page before
          subscribing or managing billing, so you don't start a second
          subscription on top of one that is already active.
        </p>
      ) : (
        <>
          <p className="mb-4 text-sm text-neutral-400">
            {active
              ? `Your plan is ${subscription?.status}${renewal ? ` — renews ${renewal}` : ""}.`
              : "No active subscription. Start a checkout session to subscribe."}
          </p>

          {error && <p className="mb-4 text-sm text-red-400">{error}</p>}

          <Button
            onClick={() => redirectTo(active ? openBillingPortal : startCheckout)}
            disabled={pending}
          >
            {pending ? "Redirecting…" : active ? "Manage billing" : "Subscribe"}
          </Button>
        </>
      )}
    </Card>
  );
}

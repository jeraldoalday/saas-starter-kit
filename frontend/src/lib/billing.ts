import { apiRequest } from "@/lib/api-client";
import { getAccessToken } from "@/lib/auth";
import type { Subscription } from "@/lib/types";

/** Subscription statuses that grant access to paid features. */
const ACTIVE_STATUSES = ["active", "trialing"];

export function isSubscriptionActive(subscription: Subscription | null): boolean {
  return subscription !== null && ACTIVE_STATUSES.includes(subscription.status);
}

export function fetchSubscription(): Promise<Subscription | null> {
  return apiRequest<Subscription | null>("/billing/subscription", {
    token: getAccessToken(),
  });
}

export async function startCheckout(): Promise<string> {
  const { checkout_url } = await apiRequest<{ checkout_url: string }>("/billing/checkout", {
    method: "POST",
    token: getAccessToken(),
  });
  return checkout_url;
}

export async function openBillingPortal(): Promise<string> {
  const { portal_url } = await apiRequest<{ portal_url: string }>("/billing/portal", {
    method: "POST",
    token: getAccessToken(),
  });
  return portal_url;
}

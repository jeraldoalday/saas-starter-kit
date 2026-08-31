import { apiRequest } from "@/lib/api-client";
import { getAccessToken } from "@/lib/auth";
import type {
  CreatedWebhookEndpoint,
  WebhookDelivery,
  WebhookEndpoint,
  WebhookEventType,
  WebhookSecret,
} from "@/lib/types";

/**
 * Outbound webhooks: the URLs this account has asked us to notify.
 *
 * Every call goes out with the session token, never with an API key — the
 * API refuses key-authenticated requests on these routes, so a leaked key
 * cannot point this account's events at a server the attacker controls, nor
 * read the signing secret that would let it forge deliveries to the real
 * receiver.
 */

/** Statuses a delivery can be in, as the API spells them. */
export const DELIVERY_PENDING = "pending";
export const DELIVERY_DELIVERED = "delivered";
export const DELIVERY_FAILED = "failed";

export function fetchWebhookEndpoints(): Promise<WebhookEndpoint[]> {
  return apiRequest<WebhookEndpoint[]>("/webhooks/endpoints", {
    token: getAccessToken(),
  });
}

/**
 * The subscribable events and what causes each.
 *
 * Unauthenticated on the API, but sent with the token anyway for
 * consistency with every other call here — the server ignores it.
 */
export function fetchWebhookEventTypes(): Promise<WebhookEventType[]> {
  return apiRequest<WebhookEventType[]>("/webhooks/event-types", {
    token: getAccessToken(),
  });
}

export function createWebhookEndpoint(
  url: string,
  description: string,
  eventTypes: string[]
): Promise<CreatedWebhookEndpoint> {
  return apiRequest<CreatedWebhookEndpoint>("/webhooks/endpoints", {
    method: "POST",
    body: { url, description, event_types: eventTypes },
    token: getAccessToken(),
  });
}

export function updateWebhookEndpoint(
  endpointId: string,
  changes: { description?: string; event_types?: string[]; is_active?: boolean }
): Promise<WebhookEndpoint> {
  return apiRequest<WebhookEndpoint>(`/webhooks/endpoints/${endpointId}`, {
    method: "PATCH",
    body: changes,
    token: getAccessToken(),
  });
}

export function deleteWebhookEndpoint(endpointId: string): Promise<void> {
  return apiRequest<void>(`/webhooks/endpoints/${endpointId}`, {
    method: "DELETE",
    token: getAccessToken(),
  });
}

export function revealWebhookSecret(endpointId: string): Promise<WebhookSecret> {
  return apiRequest<WebhookSecret>(`/webhooks/endpoints/${endpointId}/secret`, {
    token: getAccessToken(),
  });
}

/**
 * Replace an endpoint's signing secret.
 *
 * Takes effect on the next delivery, with no overlap window: a receiver
 * still checking the old secret rejects everything until it is updated.
 * The page says so before asking for a confirmation, because finding out
 * from a customer's failed deliveries is a bad way to learn it.
 */
export function rotateWebhookSecret(endpointId: string): Promise<WebhookSecret> {
  return apiRequest<WebhookSecret>(
    `/webhooks/endpoints/${endpointId}/rotate-secret`,
    { method: "POST", token: getAccessToken() }
  );
}

/** Queue a test delivery. Accepted, not sent — the worker sends it. */
export function pingWebhookEndpoint(endpointId: string): Promise<WebhookDelivery> {
  return apiRequest<WebhookDelivery>(`/webhooks/endpoints/${endpointId}/ping`, {
    method: "POST",
    token: getAccessToken(),
  });
}

export function fetchWebhookDeliveries(
  endpointId: string | null = null,
  limit = 25
): Promise<WebhookDelivery[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (endpointId !== null) params.set("endpoint_id", endpointId);
  return apiRequest<WebhookDelivery[]>(`/webhooks/deliveries?${params}`, {
    token: getAccessToken(),
  });
}

/**
 * Why an endpoint is not receiving anything, in one word.
 *
 * Three states rather than two, because the difference matters to whoever
 * is looking: "Off" is something they did and can undo, "Failing" is
 * something that happened to them and needs their receiver fixed first.
 */
export function endpointStatusLabel(endpoint: WebhookEndpoint): string {
  if (endpoint.disabled_at !== null) return "Failing";
  if (!endpoint.is_active) return "Off";
  return "Active";
}

/**
 * A sentence explaining a non-active endpoint, or null when it is fine.
 *
 * The auto-disable reason comes from the server and names the last error,
 * which is the one piece of information that turns "it stopped working"
 * into something the owner can act on.
 */
export function endpointStatusDetail(endpoint: WebhookEndpoint): string | null {
  if (endpoint.disabled_at !== null) {
    return (
      endpoint.disabled_reason ??
      "Deliveries failed repeatedly and this endpoint was switched off."
    );
  }
  if (!endpoint.is_active) return "Switched off. No events are being sent.";
  return null;
}

/** Colour class for a delivery's status cell. */
export function deliveryStatusTone(status: string): string {
  if (status === DELIVERY_DELIVERED) return "text-emerald-400";
  if (status === DELIVERY_FAILED) return "text-red-400";
  return "text-amber-400";
}

/**
 * What actually happened to a delivery, for the log's outcome column.
 *
 * The distinction between "the handler ran and threw" and "we never reached
 * it" is the first thing anyone debugging a webhook wants, and the API
 * carries it as a status code that is null in the second case. Losing that
 * to a generic "failed" would throw away the answer.
 */
export function deliveryOutcome(delivery: WebhookDelivery): string {
  if (delivery.status === DELIVERY_DELIVERED) {
    return delivery.last_status_code === null
      ? "Delivered"
      : `Delivered (${delivery.last_status_code})`;
  }
  if (delivery.last_status_code !== null) {
    return `HTTP ${delivery.last_status_code}`;
  }
  if (delivery.last_error !== null) return delivery.last_error;
  return delivery.status === DELIVERY_PENDING ? "Queued" : "No response";
}

/**
 * Human wording for an event type, falling back to the raw name.
 *
 * The fallback matters: a delivery log can hold an event from a build that
 * has since removed it, and a missing label should be a cell showing the
 * dotted name rather than an empty one.
 */
export function eventTypeLabel(
  eventType: string,
  catalog: WebhookEventType[]
): string {
  return catalog.find((entry) => entry.event_type === eventType)?.label ?? eventType;
}

/** Sort order for the table: deliverable endpoints first, then newest. */
export function sortEndpoints(endpoints: WebhookEndpoint[]): WebhookEndpoint[] {
  return [...endpoints].sort((left, right) => {
    const deliverable = Number(right.is_deliverable) - Number(left.is_deliverable);
    if (deliverable !== 0) return deliverable;
    return new Date(right.created_at).getTime() - new Date(left.created_at).getTime();
  });
}

/**
 * Whether a URL is worth sending to the API at all.
 *
 * A deliberately loose check: the server owns the real rules (scheme, port,
 * and whether the host resolves somewhere private), and duplicating them
 * here would produce a client that refuses URLs the server would accept the
 * day either list changes. This catches the typo — a missing scheme — early
 * enough to save a round trip, and nothing else.
 */
export function looksLikeUrl(value: string): boolean {
  const trimmed = value.trim();
  return /^https?:\/\/[^\s/]+/i.test(trimmed);
}

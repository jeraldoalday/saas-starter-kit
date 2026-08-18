import { apiRequest } from "@/lib/api-client";
import { getAccessToken } from "@/lib/auth";
import type { EventProperties, UsageSummary } from "@/lib/types";

/**
 * Client-side usage tracking.
 *
 * Events are buffered and flushed together rather than sent one request per
 * event: a dashboard render can emit several, and the backend ingest
 * endpoint is rate limited per minute, not per event.
 *
 * Tracking is best-effort throughout. A failed flush drops the batch and
 * resolves — analytics must never surface an error to someone who was just
 * trying to use the product.
 */

/** Mirrors the pattern the API enforces: lowercase, dotted namespaces. */
const EVENT_NAME_PATTERN = /^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$/;

/** Flush once the buffer reaches this many events, without waiting. */
const MAX_BUFFERED_EVENTS = 20;

/** How long a quiet buffer waits before going out anyway. */
const FLUSH_DELAY_MS = 2000;

interface QueuedEvent {
  name: string;
  properties: EventProperties;
}

let buffer: QueuedEvent[] = [];
let flushTimer: ReturnType<typeof setTimeout> | null = null;

function cancelScheduledFlush(): void {
  if (flushTimer !== null) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
}

function scheduleFlush(): void {
  if (flushTimer !== null) return;
  flushTimer = setTimeout(() => {
    flushTimer = null;
    void flushEvents();
  }, FLUSH_DELAY_MS);
}

/**
 * Queue one event for the next flush.
 *
 * Returns whether it was queued. A malformed name is rejected here rather
 * than at the API, because the server answers 422 for the whole batch —
 * one bad name would take every other event in the buffer down with it.
 */
export function trackEvent(name: string, properties: EventProperties = {}): boolean {
  if (!EVENT_NAME_PATTERN.test(name)) {
    console.warn(`[analytics] ignoring malformed event name: ${name}`);
    return false;
  }

  // Ingest is authenticated, so anonymous events have nowhere to go. Drop
  // them instead of buffering a batch that can only ever 401.
  if (!getAccessToken()) return false;

  buffer.push({ name, properties });

  if (buffer.length >= MAX_BUFFERED_EVENTS) {
    cancelScheduledFlush();
    void flushEvents();
  } else {
    scheduleFlush();
  }

  return true;
}

/** Send everything buffered. Resolves with how many events the API stored. */
export async function flushEvents(): Promise<number> {
  cancelScheduledFlush();

  if (buffer.length === 0) return 0;

  // Taken before the await so events tracked mid-flight land in the next
  // batch rather than being dropped by the reset below.
  const batch = buffer;
  buffer = [];

  try {
    const { recorded } = await apiRequest<{ recorded: number }>("/analytics/events", {
      method: "POST",
      body: { events: batch },
      token: getAccessToken(),
    });
    return recorded;
  } catch {
    // Deliberately not re-queued: a failing endpoint would otherwise grow
    // the buffer without bound for the rest of the session.
    return 0;
  }
}

/** Number of events waiting to be sent. Exposed for tests and debugging. */
export function pendingEventCount(): number {
  return buffer.length;
}

/** Drop everything buffered without sending it — used when logging out. */
export function resetAnalytics(): void {
  cancelScheduledFlush();
  buffer = [];
}

/**
 * Flush on the way out of the page.
 *
 * `pagehide` rather than `unload`, which is unreliable on mobile Safari and
 * blocks the back/forward cache. sendBeacon would survive the teardown more
 * reliably but cannot carry an Authorization header, and the ingest
 * endpoint requires one, so this is a best-effort request.
 */
export function startAnalytics(): () => void {
  const handler = () => {
    void flushEvents();
  };
  window.addEventListener("pagehide", handler);
  return () => window.removeEventListener("pagehide", handler);
}

export function fetchMyUsage(days = 14): Promise<UsageSummary> {
  return apiRequest<UsageSummary>(`/analytics/me?days=${days}`, {
    token: getAccessToken(),
  });
}

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  fetchMyUsage,
  flushEvents,
  humanizeEventName,
  pendingEventCount,
  resetAnalytics,
  startAnalytics,
  trackEvent,
} from "@/lib/analytics";
import { storeTokens } from "@/lib/auth";
import type { TokenPair, UsageSummary } from "@/lib/types";

const tokens: TokenPair = {
  access_token: "access-1",
  refresh_token: "refresh-1",
  token_type: "bearer",
};

const summary: UsageSummary = {
  window_days: 7,
  total_events: 3,
  active_users: 1,
  events_by_name: { "dashboard.viewed": 3 },
  daily: [{ day: "2026-08-19", count: 3 }],
};

/** Queues one successful JSON response per argument, in call order. */
function mockFetch(...bodies: unknown[]) {
  const fetchMock = vi.fn();
  for (const body of bodies) {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => body,
    });
  }
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function bodyOf(fetchMock: ReturnType<typeof vi.fn>, call = 0) {
  return JSON.parse(fetchMock.mock.calls[call][1].body);
}

beforeEach(() => {
  localStorage.clear();
  resetAnalytics();
  storeTokens(tokens);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  resetAnalytics();
});

describe("trackEvent", () => {
  it("buffers instead of sending one request per event", () => {
    const fetchMock = mockFetch({ recorded: 1 });

    expect(trackEvent("dashboard.viewed")).toBe(true);

    expect(pendingEventCount()).toBe(1);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["Dashboard Viewed", "dashboard viewed", "9lives", "", "dashboard..viewed"])(
    "refuses the malformed name %o",
    (name) => {
      // The API answers 422 for the whole batch, so one bad name would take
      // every other buffered event down with it.
      const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

      expect(trackEvent(name)).toBe(false);

      expect(pendingEventCount()).toBe(0);
      warn.mockRestore();
    }
  );

  it("drops events when there is no session to attribute them to", () => {
    localStorage.clear();

    expect(trackEvent("dashboard.viewed")).toBe(false);
    expect(pendingEventCount()).toBe(0);
  });

  it("flushes as soon as the buffer fills, without waiting for the timer", async () => {
    const fetchMock = mockFetch({ recorded: 20 });

    for (let index = 0; index < 20; index += 1) {
      trackEvent("dashboard.viewed");
    }
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());

    expect(bodyOf(fetchMock).events).toHaveLength(20);
    expect(pendingEventCount()).toBe(0);
  });

  it("flushes a quiet buffer once the delay elapses", async () => {
    vi.useFakeTimers();
    const fetchMock = mockFetch({ recorded: 1 });

    trackEvent("dashboard.viewed");
    await vi.advanceTimersByTimeAsync(2000);

    expect(fetchMock).toHaveBeenCalledOnce();
  });
});

describe("flushEvents", () => {
  it("sends the buffer as one authorized batch", async () => {
    const fetchMock = mockFetch({ recorded: 2 });
    trackEvent("dashboard.viewed", { tab: "billing" });
    trackEvent("billing.card_clicked");

    const recorded = await flushEvents();

    expect(recorded).toBe(2);
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0][0]).toContain("/analytics/events");
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer access-1");
    expect(bodyOf(fetchMock)).toEqual({
      events: [
        { name: "dashboard.viewed", properties: { tab: "billing" } },
        { name: "billing.card_clicked", properties: {} },
      ],
    });
  });

  it("does nothing when there is nothing buffered", async () => {
    const fetchMock = mockFetch({ recorded: 0 });

    expect(await flushEvents()).toBe(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("swallows failures rather than surfacing them to the caller", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    trackEvent("dashboard.viewed");

    await expect(flushEvents()).resolves.toBe(0);
  });

  it("drops a failed batch instead of growing the buffer forever", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    trackEvent("dashboard.viewed");

    await flushEvents();

    expect(pendingEventCount()).toBe(0);
  });

  it("keeps events tracked mid-flight for the next batch", async () => {
    let release: (value: unknown) => void = () => {};
    const pending = new Promise((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async () => {
        await pending;
        return { ok: true, status: 200, statusText: "OK", json: async () => ({ recorded: 1 }) };
      })
    );

    trackEvent("dashboard.viewed");
    const inFlight = flushEvents();
    trackEvent("billing.card_clicked");
    release(undefined);
    await inFlight;

    expect(pendingEventCount()).toBe(1);
  });
});

describe("resetAnalytics", () => {
  it("discards buffered events without sending them", async () => {
    const fetchMock = mockFetch({ recorded: 1 });
    trackEvent("dashboard.viewed");

    resetAnalytics();
    await flushEvents();

    expect(pendingEventCount()).toBe(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("startAnalytics", () => {
  it("flushes when the page goes away", async () => {
    const fetchMock = mockFetch({ recorded: 1 });
    const stop = startAnalytics();
    trackEvent("dashboard.viewed");

    window.dispatchEvent(new Event("pagehide"));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());

    stop();
  });

  it("stops listening once torn down", async () => {
    const fetchMock = mockFetch({ recorded: 1 });
    const stop = startAnalytics();
    stop();
    trackEvent("dashboard.viewed");

    window.dispatchEvent(new Event("pagehide"));

    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("fetchMyUsage", () => {
  it("requests the given window with the stored token", async () => {
    const fetchMock = mockFetch(summary);

    const result = await fetchMyUsage(7);

    expect(result).toEqual(summary);
    expect(fetchMock.mock.calls[0][0]).toContain("/analytics/me?days=7");
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer access-1");
  });

  it("defaults to a fortnight", async () => {
    const fetchMock = mockFetch(summary);

    await fetchMyUsage();

    expect(fetchMock.mock.calls[0][0]).toContain("days=14");
  });
});

describe("humanizeEventName", () => {
  it.each([
    ["billing.checkout_started", "Billing: checkout started"],
    ["user.registered", "User: registered"],
    ["landing", "landing"],
    ["account.status_changed", "Account: status changed"],
  ])("renders %o as %o", (raw, expected) => {
    expect(humanizeEventName(raw)).toBe(expected);
  });
});

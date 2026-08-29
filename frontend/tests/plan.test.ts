import { afterEach, describe, expect, it, vi } from "vitest";

import { storeTokens } from "@/lib/auth";
import {
  WARNING_THRESHOLD,
  describePlanStatus,
  fetchPlan,
  fetchPlanCatalog,
  findQuota,
  formatLimit,
  formatPeriodEnd,
  formatPrice,
  formatUsage,
  isPaidPlan,
  pressuredQuotas,
  quotaFraction,
  quotaTone,
} from "@/lib/plan";
import type { Plan, PlanCatalogEntry, Quota, TokenPair } from "@/lib/types";

const tokens: TokenPair = {
  access_token: "access-1",
  refresh_token: "refresh-1",
  token_type: "bearer",
};

function quota(overrides: Partial<Quota> = {}): Quota {
  return {
    key: "api_keys",
    label: "active API keys",
    limit: 20,
    used: 3,
    remaining: 17,
    exhausted: false,
    ...overrides,
  };
}

function plan(overrides: Partial<Plan> = {}): Plan {
  return {
    entitlements: {
      plan_key: "free",
      display_name: "Free",
      max_active_api_keys: 2,
      max_active_sessions: 3,
      monthly_event_allowance: 10_000,
      audit_history_days: 30,
    },
    subscription_status: null,
    quotas: [quota()],
    period_start: "2026-08-01T00:00:00Z",
    period_end: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

function mockFetch(body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: "OK",
    json: async () => body,
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("fetchPlan", () => {
  it("sends the access token", async () => {
    storeTokens(tokens);
    const fetchMock = mockFetch(plan());

    await fetchPlan();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/plan");
    expect(init.headers.Authorization).toBe("Bearer access-1");
  });

  it("reads the catalog", async () => {
    storeTokens(tokens);
    const entries: PlanCatalogEntry[] = [];
    mockFetch(entries);

    expect(await fetchPlanCatalog()).toEqual([]);
  });
});

describe("findQuota", () => {
  it("returns the named quota", () => {
    expect(findQuota(plan(), "api_keys")?.key).toBe("api_keys");
  });

  it("returns null for a quota this build does not know", () => {
    // A quota added on the backend and not here must not blow up a render.
    expect(findQuota(plan(), "seats")).toBeNull();
  });
});

describe("formatLimit", () => {
  it("renders a null limit as a promise rather than missing data", () => {
    expect(formatLimit(null)).toBe("Unlimited");
  });

  it("groups large numbers", () => {
    expect(formatLimit(1_000_000)).toBe((1_000_000).toLocaleString());
  });
});

describe("formatUsage", () => {
  it("reads as a fraction when there is a ceiling", () => {
    expect(formatUsage(quota({ used: 3, limit: 20 }))).toBe("3 of 20");
  });

  it("drops the denominator when there is none", () => {
    expect(formatUsage(quota({ limit: null, used: 42 }))).toBe("42 used");
  });
});

describe("quotaFraction", () => {
  it("is the share of the ceiling that is spent", () => {
    expect(quotaFraction(quota({ used: 5, limit: 20 }))).toBe(0.25);
  });

  it("is zero when there is no ceiling to be a fraction of", () => {
    expect(quotaFraction(quota({ limit: null, used: 900 }))).toBe(0);
  });

  it("clamps an account left over its ceiling by a downgrade", () => {
    // A bar that overflows its track is a rendering bug, not information.
    expect(quotaFraction(quota({ used: 40, limit: 20 }))).toBe(1);
  });

  it("treats a zero ceiling as empty rather than dividing by it", () => {
    expect(quotaFraction(quota({ used: 0, limit: 0 }))).toBe(0);
  });
});

describe("quotaTone", () => {
  it("is normal well below the ceiling", () => {
    expect(quotaTone(quota({ used: 1, limit: 20 }))).toBe("normal");
  });

  it("warns before anything has broken", () => {
    // The whole point of the band: a meter that only turns red once the
    // product has stopped working told the reader nothing in time.
    const used = Math.ceil(20 * WARNING_THRESHOLD);
    expect(quotaTone(quota({ used, limit: 20 }))).toBe("warning");
  });

  it("reports exhaustion from the API's own verdict", () => {
    expect(quotaTone(quota({ used: 20, limit: 20, exhausted: true }))).toBe("exhausted");
  });
});

describe("pressuredQuotas", () => {
  it("lists only quotas worth mentioning, fullest first", () => {
    const quotas = [
      quota({ key: "sessions", used: 1, limit: 10 }),
      quota({ key: "events", used: 9_500, limit: 10_000 }),
      quota({ key: "api_keys", used: 2, limit: 2, exhausted: true }),
    ];

    expect(pressuredQuotas(plan({ quotas })).map((entry) => entry.key)).toEqual([
      "api_keys",
      "events",
    ]);
  });

  it("is empty when nothing is under pressure", () => {
    expect(pressuredQuotas(plan())).toEqual([]);
  });
});

describe("formatPrice", () => {
  it("calls a missing price Free rather than zero", () => {
    // The free tier is not a Stripe product, so a 0.00 line item would be a
    // charge that does not exist.
    expect(formatPrice(null)).toBe("Free");
  });

  it("drops the cents on a whole amount", () => {
    const formatted = formatPrice({ unit_amount: 2900, currency: "usd", interval: "month" });
    expect(formatted).toContain("29");
    expect(formatted).not.toContain(".00");
    expect(formatted).toContain("month");
  });

  it("keeps the cents when there are any", () => {
    expect(
      formatPrice({ unit_amount: 2950, currency: "usd", interval: "month" })
    ).toContain("29.50");
  });

  it("falls back rather than throwing on a currency Intl rejects", () => {
    const formatted = formatPrice({
      unit_amount: 100,
      currency: "not-a-currency",
      interval: "month",
    });
    expect(formatted).toContain("NOT-A-CURRENCY");
  });
});

describe("describePlanStatus", () => {
  it("says nothing alarming about an account that never subscribed", () => {
    expect(describePlanStatus(plan())).toBe("You are on the Free plan.");
  });

  it("distinguishes a declined card from never having subscribed", () => {
    // Same plan, completely different situation — and only one of them is
    // worth interrupting somebody about.
    expect(describePlanStatus(plan({ subscription_status: "past_due" }))).toContain(
      "payment failed"
    );
  });

  it("names a cancellation", () => {
    expect(describePlanStatus(plan({ subscription_status: "canceled" }))).toContain(
      "canceled"
    );
  });

  it("calls a trial a trial", () => {
    expect(describePlanStatus(plan({ subscription_status: "trialing" }))).toContain(
      "trialling"
    );
  });

  it("falls back to a plain sentence for a status it has no wording for", () => {
    expect(describePlanStatus(plan({ subscription_status: "incomplete" }))).toContain(
      "Free plan"
    );
  });
});

describe("isPaidPlan", () => {
  it.each([
    ["active", true],
    ["trialing", true],
    ["past_due", false],
    ["canceled", false],
  ])("treats %s as paid=%s", (status, expected) => {
    expect(isPaidPlan(plan({ subscription_status: status }))).toBe(expected);
  });

  it("treats no subscription as unpaid", () => {
    expect(isPaidPlan(plan())).toBe(false);
  });
});

describe("formatPeriodEnd", () => {
  it("renders a local date", () => {
    expect(formatPeriodEnd(plan())).toBe(
      new Date("2026-09-01T00:00:00Z").toLocaleDateString()
    );
  });

  it("returns null rather than Invalid Date", () => {
    // The sentence it appears in is decoration; a broken clock should cost
    // the sentence, not the panel.
    expect(formatPeriodEnd(plan({ period_end: "nonsense" }))).toBeNull();
  });
});

import { describe, expect, it } from "vitest";

import { isSubscriptionActive } from "@/lib/billing";
import type { Subscription } from "@/lib/types";

function subscription(status: string): Subscription {
  return { id: "sub_1", status, current_period_end: null };
}

describe("isSubscriptionActive", () => {
  it("is false when there is no subscription", () => {
    expect(isSubscriptionActive(null)).toBe(false);
  });

  it.each(["active", "trialing"])("is true for %s", (status) => {
    expect(isSubscriptionActive(subscription(status))).toBe(true);
  });

  it.each(["past_due", "canceled", "incomplete"])("is false for %s", (status) => {
    expect(isSubscriptionActive(subscription(status))).toBe(false);
  });
});

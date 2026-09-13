import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BillingCard } from "@/components/BillingCard";
import type { Subscription } from "@/lib/types";

vi.mock("@/lib/billing", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/billing")>();
  return { ...actual, fetchSubscription: vi.fn() };
});

const { fetchSubscription } = await import("@/lib/billing");
const fetchSubscriptionMock = vi.mocked(fetchSubscription);

function subscription(overrides: Partial<Subscription> = {}): Subscription {
  return {
    id: "sub_1",
    status: "active",
    current_period_end: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("BillingCard", () => {
  it("offers to subscribe when there is no subscription", async () => {
    fetchSubscriptionMock.mockResolvedValue(null);

    render(<BillingCard />);

    expect(
      await screen.findByText("No active subscription. Start a checkout session to subscribe.")
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Subscribe" })).toBeInTheDocument();
  });

  it("offers to manage billing for an active subscription", async () => {
    fetchSubscriptionMock.mockResolvedValue(subscription());

    render(<BillingCard />);

    expect(await screen.findByText(/Your plan is active/)).toBeInTheDocument();
    expect(screen.getByText(/renews/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Manage billing" })).toBeInTheDocument();
  });

  it("does not claim there is no subscription when the status failed to load", async () => {
    // A network failure tells us nothing about billing status — it must not
    // be read as "confirmed unsubscribed", which would offer a "Subscribe"
    // button to someone who already has an active subscription and risk
    // them paying for a second one.
    fetchSubscriptionMock.mockRejectedValue(new Error("offline"));

    render(<BillingCard />);

    expect(
      await screen.findByText(/Could not load your subscription status/)
    ).toBeInTheDocument();
    expect(
      screen.queryByText("No active subscription. Start a checkout session to subscribe.")
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Subscribe" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Manage billing" })).not.toBeInTheDocument();
  });

  it("treats a trial as active, not as unsubscribed", async () => {
    fetchSubscriptionMock.mockResolvedValue(subscription({ status: "trialing" }));

    render(<BillingCard />);

    expect(await screen.findByText(/Your plan is trialing/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Manage billing" })).toBeInTheDocument();
  });
});

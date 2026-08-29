import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PlanCard } from "@/components/PlanCard";
import type { Plan, Quota } from "@/lib/types";

vi.mock("@/lib/plan", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/plan")>();
  return { ...actual, fetchPlan: vi.fn() };
});

const { fetchPlan } = await import("@/lib/plan");
const fetchPlanMock = vi.mocked(fetchPlan);

function quota(overrides: Partial<Quota> = {}): Quota {
  return {
    key: "api_keys",
    label: "active API keys",
    limit: 2,
    used: 1,
    remaining: 1,
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
    quotas: [
      quota(),
      quota({ key: "sessions", label: "signed-in devices", limit: 3, used: 1, remaining: 2 }),
      quota({
        key: "events",
        label: "events this month",
        limit: 10_000,
        used: 12,
        remaining: 9_988,
      }),
    ],
    period_start: "2026-08-01T00:00:00Z",
    period_end: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("PlanCard", () => {
  it("names the plan and says why the account is on it", async () => {
    fetchPlanMock.mockResolvedValue(plan());

    render(<PlanCard />);

    expect(await screen.findByText("You are on the Free plan.")).toBeInTheDocument();
    expect(screen.getByText("Free")).toBeInTheDocument();
  });

  it("draws a labelled meter for every quota", async () => {
    fetchPlanMock.mockResolvedValue(plan());

    render(<PlanCard />);

    const meters = await screen.findAllByRole("progressbar");
    expect(meters).toHaveLength(3);
    // Announced as the fact, not as the shape of the drawing.
    expect(meters[0]).toHaveAttribute("aria-valuetext", "1 of 2");
    expect(meters[0]).toHaveAttribute("aria-label", "active API keys");
  });

  it("warns before a quota is spent, not only after", async () => {
    fetchPlanMock.mockResolvedValue(
      plan({
        quotas: [quota({ used: 9_600, limit: 10_000, label: "events this month" })],
      })
    );

    render(<PlanCard />);

    expect(
      await screen.findByText("You are close to your limit on events this month.")
    ).toBeInTheDocument();
  });

  it("says plainly when a quota is spent", async () => {
    fetchPlanMock.mockResolvedValue(
      plan({ quotas: [quota({ used: 2, remaining: 0, exhausted: true })] })
    );

    render(<PlanCard />);

    expect(
      await screen.findByText("You have used all of your active API keys.")
    ).toBeInTheDocument();
  });

  it("says nothing when every quota has room", async () => {
    fetchPlanMock.mockResolvedValue(plan());

    render(<PlanCard />);

    await screen.findByText("You are on the Free plan.");
    expect(screen.queryByText(/close to your limit/)).not.toBeInTheDocument();
  });

  it("explains a plan that lapsed rather than one that never existed", async () => {
    fetchPlanMock.mockResolvedValue(plan({ subscription_status: "past_due" }));

    render(<PlanCard />);

    expect(await screen.findByText(/payment failed/)).toBeInTheDocument();
  });

  it("shows the history window and when the allowance resets", async () => {
    fetchPlanMock.mockResolvedValue(plan());

    render(<PlanCard />);

    expect(await screen.findByText("Security history")).toBeInTheDocument();
    expect(screen.getByText("30 days")).toBeInTheDocument();
    expect(screen.getByText("Event allowance resets")).toBeInTheDocument();
  });

  it("renders an unlimited entitlement as a word, not a dash", async () => {
    fetchPlanMock.mockResolvedValue(
      plan({
        entitlements: { ...plan().entitlements, audit_history_days: null },
      })
    );

    render(<PlanCard />);

    expect(await screen.findByText("Unlimited")).toBeInTheDocument();
  });

  it("degrades to a message instead of throwing when the request fails", async () => {
    fetchPlanMock.mockRejectedValue(new Error("offline"));

    render(<PlanCard />);

    expect(
      await screen.findByText("Plan details are unavailable right now.")
    ).toBeInTheDocument();
  });
});

import { render, screen, waitFor } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { UsageCard } from "@/components/UsageCard";
import type { UsageSummary } from "@/lib/types";

vi.mock("@/lib/analytics", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/analytics")>();
  return { ...actual, fetchMyUsage: vi.fn() };
});

const { fetchMyUsage } = await import("@/lib/analytics");
const fetchMyUsageMock = vi.mocked(fetchMyUsage);

function summary(overrides: Partial<UsageSummary> = {}): UsageSummary {
  return {
    window_days: 14,
    total_events: 6,
    active_users: 1,
    events_by_name: { "dashboard.viewed": 4, "billing.checkout_started": 2 },
    daily: [
      { day: "2026-08-18", count: 2 },
      { day: "2026-08-19", count: 4 },
    ],
    ...overrides,
  };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("UsageCard", () => {
  it("shows the total and the most frequent events", async () => {
    fetchMyUsageMock.mockResolvedValue(summary());

    render(<UsageCard />);

    expect(await screen.findByText("6 events in the last 14 days.")).toBeInTheDocument();
    expect(screen.getByText("Dashboard: viewed")).toBeInTheDocument();
    expect(screen.getByText("Billing: checkout started")).toBeInTheDocument();
  });

  it("says so plainly when nothing was recorded", async () => {
    fetchMyUsageMock.mockResolvedValue(
      summary({ total_events: 0, events_by_name: {}, daily: [{ day: "2026-08-19", count: 0 }] })
    );

    render(<UsageCard />);

    expect(
      await screen.findByText("No recorded activity in the last 14 days.")
    ).toBeInTheDocument();
  });

  it("degrades to a message instead of throwing when the request fails", async () => {
    fetchMyUsageMock.mockRejectedValue(new Error("offline"));

    render(<UsageCard />);

    expect(
      await screen.findByText("Usage data is unavailable right now.")
    ).toBeInTheDocument();
  });

  it("defaults to the fortnight window", async () => {
    fetchMyUsageMock.mockResolvedValue(summary());

    render(<UsageCard />);

    await waitFor(() => expect(fetchMyUsageMock).toHaveBeenCalledWith(14));
    expect(screen.getByRole("button", { name: "14d" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
  });

  it("refetches when another window is chosen", async () => {
    fetchMyUsageMock.mockResolvedValue(summary({ window_days: 30 }));

    render(<UsageCard />);
    await screen.findByRole("button", { name: "30d" });
    fireEvent.click(screen.getByRole("button", { name: "30d" }));

    await waitFor(() => expect(fetchMyUsageMock).toHaveBeenLastCalledWith(30));
    expect(screen.getByRole("button", { name: "30d" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
  });

  it("renders the daily series as a labelled chart", async () => {
    fetchMyUsageMock.mockResolvedValue(summary());

    render(<UsageCard />);

    expect(
      await screen.findByRole("img", { name: "Daily activity over the last 14 days" })
    ).toBeInTheDocument();
  });

  it("lists at most five events", async () => {
    fetchMyUsageMock.mockResolvedValue(
      summary({
        events_by_name: Object.fromEntries(
          Array.from({ length: 9 }, (_, index) => [`feature.used_${index}`, 9 - index])
        ),
      })
    );

    render(<UsageCard />);

    await screen.findByText("Feature: used 0");
    expect(screen.queryByText("Feature: used 5")).not.toBeInTheDocument();
  });
});

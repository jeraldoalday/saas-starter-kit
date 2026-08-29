import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SecurityPage } from "@/pages/SecurityPage";
import type { AuditEntry, AuditPage } from "@/lib/types";

vi.mock("@/lib/audit", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/audit")>();
  return { ...actual, fetchAuditPage: vi.fn() };
});

vi.mock("@/lib/analytics", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/analytics")>();
  return { ...actual, trackEvent: vi.fn() };
});

const { fetchAuditPage } = await import("@/lib/audit");
const fetchAuditPageMock = vi.mocked(fetchAuditPage);

const CHROME_MAC =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36";

function entry(overrides: Partial<AuditEntry> = {}): AuditEntry {
  return {
    id: "audit_1",
    action: "auth.login",
    outcome: "success",
    ip_address: "203.0.113.7",
    user_agent: CHROME_MAC,
    context: {},
    created_at: new Date().toISOString(),
    by_another_account: false,
    ...overrides,
  };
}

function page(overrides: Partial<AuditPage> = {}): AuditPage {
  return {
    entries: [entry()],
    total: 1,
    limit: 25,
    offset: 0,
    history_days: 30,
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <SecurityPage />
    </MemoryRouter>
  );
}

beforeEach(() => {
  fetchAuditPageMock.mockReset();
  fetchAuditPageMock.mockResolvedValue(page());
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("SecurityPage", () => {
  it("shows the trail in plain English rather than dotted names", async () => {
    renderPage();

    expect(await screen.findByText("Signed in")).toBeInTheDocument();
    expect(screen.queryByText("auth.login")).not.toBeInTheDocument();
  });

  it("shows the device and address each event came from", async () => {
    renderPage();

    // The two facts that make an unfamiliar sign-in recognisable as one.
    expect(await screen.findByText("Chrome on macOS")).toBeInTheDocument();
    expect(screen.getByText("203.0.113.7")).toBeInTheDocument();
  });

  it("says how much of the trail is on screen, and how far back it reaches", async () => {
    fetchAuditPageMock.mockResolvedValue(page({ total: 80, history_days: 30 }));

    renderPage();

    // The window is part of the sentence: a trail that simply stops reads
    // to its owner as rows somebody deleted.
    expect(
      await screen.findByText("Showing 1 of 80 from the last 30 days")
    ).toBeInTheDocument();
  });

  it("says nothing about a window when the plan has no limit", async () => {
    fetchAuditPageMock.mockResolvedValue(page({ total: 80, history_days: null }));

    renderPage();

    expect(await screen.findByText("Showing 1 of 80")).toBeInTheDocument();
  });

  it("surfaces refusals above the table", async () => {
    fetchAuditPageMock.mockResolvedValue(
      page({
        entries: [
          entry({ id: "ok", action: "auth.login" }),
          entry({
            id: "bad",
            action: "auth.login_failed",
            outcome: "failure",
            ip_address: "198.51.100.4",
          }),
        ],
        total: 2,
      })
    );

    renderPage();

    // "Has anything happened to my account that I did not do" is the
    // question the full table is too long to answer at a glance.
    expect(await screen.findByText("Worth a look")).toBeInTheDocument();
    expect(
      screen.getAllByText("Failed sign-in attempt").length
    ).toBeGreaterThan(1);
  });

  it("keeps the highlights panel away when nothing needs it", async () => {
    renderPage();

    await screen.findByText("Signed in");
    expect(screen.queryByText("Worth a look")).not.toBeInTheDocument();
  });

  it("marks the rows an administrator caused", async () => {
    fetchAuditPageMock.mockResolvedValue(
      page({
        entries: [
          entry({
            action: "account.role_changed",
            by_another_account: true,
            context: { from: "member", to: "admin" },
          }),
        ],
      })
    );

    renderPage();

    expect(await screen.findByText("by an administrator")).toBeInTheDocument();
    expect(screen.getByText("member → admin")).toBeInTheDocument();
  });

  it("offers another page only when one exists", async () => {
    renderPage();

    await screen.findByText("Signed in");
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
  });

  it("appends the next page rather than replacing what is shown", async () => {
    fetchAuditPageMock.mockResolvedValueOnce(page({ total: 2 }));
    fetchAuditPageMock.mockResolvedValueOnce(
      page({
        entries: [entry({ id: "audit_2", action: "auth.logout" })],
        total: 2,
        offset: 1,
      })
    );

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Load more" }));

    expect(await screen.findByText("Signed out")).toBeInTheDocument();
    expect(screen.getByText("Signed in")).toBeInTheDocument();
  });

  it("offsets by what is on screen, not by a page counter", async () => {
    fetchAuditPageMock.mockResolvedValueOnce(
      page({ entries: [entry({ id: "a" }), entry({ id: "b" })], total: 5 })
    );
    fetchAuditPageMock.mockResolvedValueOnce(page({ total: 5, offset: 2 }));

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Load more" }));

    // The trail grows while somebody reads it, and a page counter would
    // quietly skip whatever arrived in between.
    await waitFor(() => {
      expect(fetchAuditPageMock).toHaveBeenLastCalledWith(2, 25);
    });
  });

  it("hides the button once the last page has arrived", async () => {
    fetchAuditPageMock.mockResolvedValueOnce(page({ total: 2 }));
    fetchAuditPageMock.mockResolvedValueOnce(
      page({ entries: [entry({ id: "audit_2" })], total: 2, offset: 1 })
    );

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Load more" }));

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
    });
  });

  it("reports a failure to load without emptying the page", async () => {
    fetchAuditPageMock.mockRejectedValue(new Error("network down"));

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load your security history."
    );
  });

  it("keeps what is already shown when a later page fails", async () => {
    fetchAuditPageMock.mockResolvedValueOnce(page({ total: 5 }));
    fetchAuditPageMock.mockRejectedValueOnce(new Error("network down"));

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Load more" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load more history."
    );
    // Losing the rows someone was reading is a worse failure than the one
    // that just happened.
    expect(screen.getByText("Signed in")).toBeInTheDocument();
  });

  it("says so when there is nothing recorded", async () => {
    fetchAuditPageMock.mockResolvedValue(page({ entries: [], total: 0 }));

    renderPage();

    expect(await screen.findByText("Nothing recorded yet.")).toBeInTheDocument();
  });
});

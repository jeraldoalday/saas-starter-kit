import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SessionsPage } from "@/pages/SessionsPage";
import type { AuthSession } from "@/lib/types";

const navigate = vi.fn();

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("@/lib/sessions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/sessions")>();
  return {
    ...actual,
    fetchSessions: vi.fn(),
    revokeSession: vi.fn(),
    revokeAllSessions: vi.fn(),
  };
});

vi.mock("@/lib/analytics", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/analytics")>();
  return { ...actual, trackEvent: vi.fn() };
});

const { fetchSessions, revokeAllSessions, revokeSession } = await import(
  "@/lib/sessions"
);
const fetchSessionsMock = vi.mocked(fetchSessions);
const revokeSessionMock = vi.mocked(revokeSession);
const revokeAllSessionsMock = vi.mocked(revokeAllSessions);

const CHROME_MAC =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36";
const FIREFOX_LINUX =
  "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/127.0";

function futureIso(days = 14): string {
  return new Date(Date.now() + days * 86_400_000).toISOString();
}

function session(overrides: Partial<AuthSession> = {}): AuthSession {
  return {
    id: "session_1",
    created_at: "2026-08-01T12:00:00Z",
    last_used_at: new Date().toISOString(),
    expires_at: futureIso(),
    revoked_at: null,
    user_agent: CHROME_MAC,
    ip_address: "203.0.113.7",
    is_active: true,
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <SessionsPage />
    </MemoryRouter>
  );
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("saas.access_token", "access-1");
  localStorage.setItem("saas.refresh_token", "refresh-1");
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("SessionsPage", () => {
  it("describes each device rather than dumping its user agent", async () => {
    fetchSessionsMock.mockResolvedValue([session()]);

    renderPage();

    expect(await screen.findByText("Chrome on macOS")).toBeInTheDocument();
    expect(screen.getByText("203.0.113.7")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    // The raw string is noise for the person reading the list.
    expect(screen.queryByText(CHROME_MAC)).not.toBeInTheDocument();
  });

  it("never renders token material", async () => {
    fetchSessionsMock.mockResolvedValue([session()]);

    const { container } = renderPage();
    await screen.findByText("Chrome on macOS");

    expect(container.textContent).not.toContain("refresh-1");
  });

  it("shows revoked sessions as signed out with no action", async () => {
    fetchSessionsMock.mockResolvedValue([
      session({ id: "dead", revoked_at: "2026-08-10T12:00:00Z", is_active: false }),
    ]);

    renderPage();

    expect(await screen.findByText("Signed out")).toBeInTheDocument();
    // Nothing left to revoke, so no button to press.
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it("says so when there is nothing to show", async () => {
    fetchSessionsMock.mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText("No sessions to show.")).toBeInTheDocument();
  });

  it("reports a failure to load", async () => {
    fetchSessionsMock.mockRejectedValue(new Error("offline"));

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load your sessions."
    );
  });

  it("requires a second click before signing a device out", async () => {
    fetchSessionsMock.mockResolvedValue([session()]);
    revokeSessionMock.mockResolvedValue(undefined);

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));

    // First click only arms the button — revoking a session is irreversible.
    expect(revokeSessionMock).not.toHaveBeenCalled();
    expect(
      await screen.findByRole("button", { name: "Click to confirm" })
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Click to confirm" }));

    await waitFor(() => expect(revokeSessionMock).toHaveBeenCalledWith("session_1"));
  });

  it("cancels the confirmation when the button loses focus", async () => {
    fetchSessionsMock.mockResolvedValue([session()]);

    renderPage();
    const button = await screen.findByRole("button", { name: "Sign out" });
    fireEvent.click(button);
    fireEvent.blur(await screen.findByRole("button", { name: "Click to confirm" }));

    expect(await screen.findByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("reloads the list after signing a device out", async () => {
    fetchSessionsMock.mockResolvedValue([session()]);
    revokeSessionMock.mockResolvedValue(undefined);

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    fireEvent.click(await screen.findByRole("button", { name: "Click to confirm" }));

    await waitFor(() => expect(fetchSessionsMock).toHaveBeenCalledTimes(2));
  });

  it("surfaces a failure to sign a device out", async () => {
    fetchSessionsMock.mockResolvedValue([session()]);
    revokeSessionMock.mockRejectedValue(new Error("nope"));

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    fireEvent.click(await screen.findByRole("button", { name: "Click to confirm" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not sign that device out."
    );
  });

  it("offers to sign out other devices only when there are any", async () => {
    fetchSessionsMock.mockResolvedValue([session()]);

    renderPage();
    await screen.findByText("Chrome on macOS");

    expect(
      screen.queryByRole("button", { name: "Sign out other devices" })
    ).not.toBeInTheDocument();
  });

  it("signs out other devices while keeping this one", async () => {
    fetchSessionsMock.mockResolvedValue([
      session(),
      session({ id: "session_2", user_agent: FIREFOX_LINUX }),
    ]);
    revokeAllSessionsMock.mockResolvedValue({ revoked: 1 });

    renderPage();
    fireEvent.click(
      await screen.findByRole("button", { name: "Sign out other devices" })
    );
    fireEvent.click(await screen.findByRole("button", { name: "Click to confirm" }));

    // true = keep this browser signed in.
    await waitFor(() => expect(revokeAllSessionsMock).toHaveBeenCalledWith(true));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Signed out 1 other device."
    );
    // Still here — the whole point of sparing the current session.
    expect(navigate).not.toHaveBeenCalled();
  });

  it("pluralises the count of devices signed out", async () => {
    fetchSessionsMock.mockResolvedValue([
      session(),
      session({ id: "session_2" }),
      session({ id: "session_3" }),
    ]);
    revokeAllSessionsMock.mockResolvedValue({ revoked: 2 });

    renderPage();
    fireEvent.click(
      await screen.findByRole("button", { name: "Sign out other devices" })
    );
    fireEvent.click(await screen.findByRole("button", { name: "Click to confirm" }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Signed out 2 other devices."
    );
  });

  it("signing out everywhere clears the tokens and leaves", async () => {
    fetchSessionsMock.mockResolvedValue([session()]);
    revokeAllSessionsMock.mockResolvedValue({ revoked: 1 });

    renderPage();
    fireEvent.click(
      await screen.findByRole("button", { name: "Sign out everywhere" })
    );

    // false = revoke this browser's session too, so its stored tokens are
    // already dead and keeping them would only defer the failure.
    await waitFor(() => expect(revokeAllSessionsMock).toHaveBeenCalledWith(false));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/login"));
    expect(localStorage.getItem("saas.access_token")).toBeNull();
    expect(localStorage.getItem("saas.refresh_token")).toBeNull();
  });

  it("keeps the user signed in when signing out everywhere fails", async () => {
    fetchSessionsMock.mockResolvedValue([session()]);
    revokeAllSessionsMock.mockRejectedValue(new Error("offline"));

    renderPage();
    fireEvent.click(
      await screen.findByRole("button", { name: "Sign out everywhere" })
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not sign out everywhere."
    );
    // Nothing was revoked, so throwing away working tokens would sign the
    // user out of a session that is still perfectly good.
    expect(localStorage.getItem("saas.access_token")).toBe("access-1");
    expect(navigate).not.toHaveBeenCalled();
  });
});

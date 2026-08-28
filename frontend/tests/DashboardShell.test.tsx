import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { DashboardShell } from "@/components/DashboardShell";
import { NAV_ITEMS } from "@/lib/navigation";
import type { User } from "@/lib/types";

const currentUser: User = {
  id: "user_1",
  email: "someone@example.com",
  full_name: "Someone",
  is_active: true,
  is_verified: true,
  role: "member",
};

function renderShell(onLogout = vi.fn()) {
  render(
    <MemoryRouter initialEntries={["/dashboard"]}>
      <Routes>
        <Route
          path="/dashboard"
          element={
            <DashboardShell user={currentUser} onLogout={onLogout}>
              <p>Dashboard body</p>
            </DashboardShell>
          }
        />
        <Route
          path="/dashboard/api-keys"
          element={
            <DashboardShell user={currentUser} onLogout={onLogout}>
              <p>API keys body</p>
            </DashboardShell>
          }
        />
        <Route path="/login" element={<p>Login page</p>} />
      </Routes>
    </MemoryRouter>
  );
  return onLogout;
}

describe("DashboardShell", () => {
  it("renders the signed-in email and its children", () => {
    renderShell();

    expect(screen.getByText(currentUser.email)).toBeInTheDocument();
    expect(screen.getByText("Dashboard body")).toBeInTheDocument();
  });

  it("links to every dashboard route", () => {
    renderShell();

    // Driven off NAV_ITEMS rather than a hardcoded list. Restating the
    // links here would mean the test passes whatever the header actually
    // contains, which is the opposite of what a nav test is for: adding a
    // section with a typo'd destination could not fail it.
    for (const item of NAV_ITEMS) {
      expect(screen.getByRole("link", { name: item.label })).toHaveAttribute(
        "href",
        item.to
      );
    }
  });

  it("offers a link for every section and no others", () => {
    renderShell();

    const labels = screen
      .getAllByRole("link")
      .map((link) => link.textContent);

    expect(labels).toEqual(NAV_ITEMS.map((item) => item.label));
  });

  it("navigates to a nested route from the nav", () => {
    renderShell();

    fireEvent.click(screen.getByRole("link", { name: "API keys" }));

    expect(screen.getByText("API keys body")).toBeInTheDocument();
  });

  it("stops highlighting Overview once a nested route is active", () => {
    renderShell();
    fireEvent.click(screen.getByRole("link", { name: "API keys" }));

    // `end` on the Overview link is what keeps /dashboard from matching its
    // own children; without it both links read as current.
    expect(screen.getByRole("link", { name: "Overview" })).not.toHaveAttribute(
      "aria-current"
    );
    expect(screen.getByRole("link", { name: "API keys" })).toHaveAttribute(
      "aria-current",
      "page"
    );
  });

  it("clears the session and navigates to /login on logout", () => {
    const onLogout = renderShell();

    fireEvent.click(screen.getByRole("button", { name: "Log out" }));

    // Both halves matter: dropping the tokens without navigating leaves the
    // user staring at a stale dashboard, and navigating without dropping them
    // leaves a live session behind.
    expect(onLogout).toHaveBeenCalledOnce();
    expect(screen.getByText("Login page")).toBeInTheDocument();
  });
});

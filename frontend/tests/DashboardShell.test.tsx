import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { DashboardShell } from "@/components/DashboardShell";
import type { User } from "@/lib/types";

const currentUser: User = {
  id: "user_1",
  email: "someone@example.com",
  full_name: "Someone",
  is_active: true,
  is_verified: true,
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

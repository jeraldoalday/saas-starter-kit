import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProtectedRoute } from "@/components/ProtectedRoute";
import type { User } from "@/lib/types";

const currentUser: User = {
  id: "user_1",
  email: "someone@example.com",
  full_name: "Someone",
  is_active: true,
  is_verified: true,
};

function renderProtected() {
  return render(
    <MemoryRouter initialEntries={["/dashboard"]}>
      <Routes>
        <Route element={<ProtectedRoute />}>
          <Route path="/dashboard" element={<p>Protected content</p>} />
        </Route>
        <Route path="/login" element={<p>Login page</p>} />
      </Routes>
    </MemoryRouter>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("ProtectedRoute", () => {
  it("redirects to /login when there is no stored token", () => {
    renderProtected();

    expect(screen.getByText("Login page")).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
  });

  it("renders the nested route inside the shell once the session resolves", async () => {
    localStorage.setItem("saas.access_token", "token-123");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => currentUser,
      })
    );

    renderProtected();

    expect(await screen.findByText("Protected content")).toBeInTheDocument();
    expect(screen.getByText(currentUser.email)).toBeInTheDocument();
  });
});

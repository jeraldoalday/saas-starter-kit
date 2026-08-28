import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { App } from "@/App";
import { NAV_ITEMS } from "@/lib/navigation";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>
  );
}

afterEach(() => {
  localStorage.clear();
});

describe("App routing", () => {
  it("renders the landing page at /", () => {
    renderAt("/");
    expect(screen.getByRole("heading", { name: /ship your saas/i })).toBeInTheDocument();
  });

  it("renders the login form at /login", () => {
    renderAt("/login");
    expect(screen.getByRole("heading", { name: "Log in" })).toBeInTheDocument();
  });

  it("renders the register form at /register", () => {
    renderAt("/register");
    expect(screen.getByRole("heading", { name: "Create an account" })).toBeInTheDocument();
  });

  it("redirects an unknown route back to the landing page", () => {
    renderAt("/no-such-page");
    expect(screen.getByRole("heading", { name: /ship your saas/i })).toBeInTheDocument();
  });

  it("redirects /dashboard to /login when there is no session", () => {
    renderAt("/dashboard");
    expect(screen.getByRole("heading", { name: "Log in" })).toBeInTheDocument();
  });

  it.each(NAV_ITEMS.map((item) => item.to))(
    "guards %s behind a session",
    (path) => {
      // Every destination the header offers, not a sample of them: a new
      // section added outside the ProtectedRoute element would otherwise
      // ship an unauthenticated page and no test would notice.
      renderAt(path);
      expect(screen.getByRole("heading", { name: "Log in" })).toBeInTheDocument();
    }
  );

  it("sends an unknown dashboard path back to the landing page", () => {
    renderAt("/dashboard/no-such-section");
    expect(
      screen.getByRole("heading", { name: /ship your saas/i })
    ).toBeInTheDocument();
  });
});

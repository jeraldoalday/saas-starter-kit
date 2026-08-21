import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { App } from "@/App";

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

  it("guards the API keys page too", () => {
    renderAt("/dashboard/api-keys");
    expect(screen.getByRole("heading", { name: "Log in" })).toBeInTheDocument();
  });
});

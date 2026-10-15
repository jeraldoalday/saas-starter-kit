import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const authMock = vi.hoisted(() => ({
  login: vi.fn(),
  registerAccount: vi.fn(),
}));

vi.mock("@/lib/auth", () => authMock);

import { AuthForm } from "@/components/AuthForm";
import { ApiClientError } from "@/lib/api-client";

function renderForm(mode: "login" | "register") {
  render(
    <MemoryRouter initialEntries={["/auth"]}>
      <Routes>
        <Route path="/auth" element={<AuthForm mode={mode} />} />
        <Route path="/dashboard" element={<p>Dashboard page</p>} />
      </Routes>
    </MemoryRouter>
  );
}

function fillCredentials() {
  fireEvent.change(screen.getByLabelText("Email"), {
    target: { value: "someone@example.com" },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: "hunter2hunter2" },
  });
}

beforeEach(() => {
  authMock.login.mockResolvedValue(undefined);
  authMock.registerAccount.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("AuthForm in login mode", () => {
  it("has no full name field", () => {
    renderForm("login");

    expect(screen.queryByLabelText("Full name")).not.toBeInTheDocument();
  });

  it("logs in and navigates to the dashboard", async () => {
    renderForm("login");
    fillCredentials();

    fireEvent.click(screen.getByRole("button", { name: "Log in" }));

    await waitFor(() =>
      expect(authMock.login).toHaveBeenCalledWith("someone@example.com", "hunter2hunter2")
    );
    expect(authMock.registerAccount).not.toHaveBeenCalled();
    expect(await screen.findByText("Dashboard page")).toBeInTheDocument();
  });
});

describe("AuthForm in register mode", () => {
  it("registers and then logs in with the same credentials", async () => {
    renderForm("register");
    fillCredentials();

    fireEvent.click(screen.getByRole("button", { name: "Sign up" }));

    // Registering without the follow-up login would drop the user back on the
    // dashboard with no session, so the ordering is the point of this test.
    await waitFor(() => expect(authMock.registerAccount).toHaveBeenCalledOnce());
    expect(authMock.login).toHaveBeenCalledWith("someone@example.com", "hunter2hunter2");
    expect(await screen.findByText("Dashboard page")).toBeInTheDocument();
  });

  it("sends undefined rather than an empty string when no full name is typed", async () => {
    renderForm("register");
    fillCredentials();

    fireEvent.click(screen.getByRole("button", { name: "Sign up" }));

    await waitFor(() =>
      expect(authMock.registerAccount).toHaveBeenCalledWith(
        "someone@example.com",
        "hunter2hunter2",
        undefined
      )
    );
  });

  it("passes the full name through when typed", async () => {
    renderForm("register");
    fillCredentials();
    fireEvent.change(screen.getByLabelText("Full name"), { target: { value: "Someone" } });

    fireEvent.click(screen.getByRole("button", { name: "Sign up" }));

    await waitFor(() =>
      expect(authMock.registerAccount).toHaveBeenCalledWith(
        "someone@example.com",
        "hunter2hunter2",
        "Someone"
      )
    );
  });
});

describe("AuthForm error handling", () => {
  it("surfaces the server's message from an ApiClientError", async () => {
    authMock.login.mockRejectedValue(new ApiClientError(401, "Incorrect email or password"));
    renderForm("login");
    fillCredentials();

    fireEvent.click(screen.getByRole("button", { name: "Log in" }));

    expect(await screen.findByText("Incorrect email or password")).toBeInTheDocument();
    expect(screen.queryByText("Dashboard page")).not.toBeInTheDocument();
  });

  it("announces a failed sign-in to assistive tech", async () => {
    // role="alert" is what gets a screen reader to speak the failure
    // without the user having to go looking for it after submitting.
    authMock.login.mockRejectedValue(new ApiClientError(401, "Incorrect email or password"));
    renderForm("login");
    fillCredentials();

    fireEvent.click(screen.getByRole("button", { name: "Log in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect email or password");
  });

  it("falls back to a generic message for a non-API failure", async () => {
    authMock.login.mockRejectedValue(new TypeError("network down"));
    renderForm("login");
    fillCredentials();

    fireEvent.click(screen.getByRole("button", { name: "Log in" }));

    // A raw TypeError message would leak transport detail into the UI.
    expect(await screen.findByText("Something went wrong")).toBeInTheDocument();
  });

  it("re-enables the submit button after a failure so the user can retry", async () => {
    authMock.login.mockRejectedValue(new ApiClientError(500, "Server error"));
    renderForm("login");
    fillCredentials();

    fireEvent.click(screen.getByRole("button", { name: "Log in" }));

    await screen.findByText("Server error");
    expect(screen.getByRole("button", { name: "Log in" })).toBeEnabled();
  });
});

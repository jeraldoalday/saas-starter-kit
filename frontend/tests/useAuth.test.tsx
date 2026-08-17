import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuth } from "@/hooks/useAuth";
import type { User } from "@/lib/types";

const currentUser: User = {
  id: "user_1",
  email: "someone@example.com",
  full_name: "Someone",
  is_active: true,
  is_verified: true,
};

function mockFetchUser() {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: "OK",
    json: async () => currentUser,
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useAuth", () => {
  it("settles as logged out without a stored token, and never calls the API", () => {
    const fetchMock = mockFetchUser();

    const { result } = renderHook(() => useAuth());

    // loading starts false here: with no token there is nothing to resolve, so
    // consumers must not be left sitting on a spinner.
    expect(result.current.loading).toBe(false);
    expect(result.current.user).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("resolves the current user when a token is present", async () => {
    localStorage.setItem("saas.access_token", "access-1");
    mockFetchUser();

    const { result } = renderHook(() => useAuth());

    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.user).toEqual(currentUser);
  });

  it("settles as logged out when the token is rejected", async () => {
    localStorage.setItem("saas.access_token", "expired");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        statusText: "Unauthorized",
        json: async () => ({ detail: "Unauthorized" }),
      })
    );

    const { result } = renderHook(() => useAuth());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.user).toBeNull();
  });

  it("clears the user and the stored token on logout", async () => {
    localStorage.setItem("saas.access_token", "access-1");
    mockFetchUser();

    const { result } = renderHook(() => useAuth());
    await waitFor(() => expect(result.current.user).toEqual(currentUser));

    act(() => result.current.logout());

    expect(result.current.user).toBeNull();
    expect(localStorage.getItem("saas.access_token")).toBeNull();
  });
});

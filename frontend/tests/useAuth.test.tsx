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
  role: "member",
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

  it("drops a token the API rejected", async () => {
    localStorage.setItem("saas.access_token", "expired");
    localStorage.setItem("saas.refresh_token", "expired-refresh");
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
    // Left in place, the dead token makes every protected route repeat the
    // same doomed request and keeps trackEvent() queueing undeliverable
    // events, because it only checks that *a* token exists.
    expect(localStorage.getItem("saas.access_token")).toBeNull();
    expect(localStorage.getItem("saas.refresh_token")).toBeNull();
  });

  it("keeps the token when the request fails for any other reason", async () => {
    localStorage.setItem("saas.access_token", "still-good");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    const { result } = renderHook(() => useAuth());

    await waitFor(() => expect(result.current.loading).toBe(false));
    // Going through a tunnel is not a logout.
    expect(result.current.user).toBeNull();
    expect(localStorage.getItem("saas.access_token")).toBe("still-good");
  });

  it("does not drop the token on a server error", async () => {
    localStorage.setItem("saas.access_token", "still-good");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        statusText: "Service Unavailable",
        json: async () => ({ detail: "Service Unavailable" }),
      })
    );

    const { result } = renderHook(() => useAuth());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(localStorage.getItem("saas.access_token")).toBe("still-good");
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

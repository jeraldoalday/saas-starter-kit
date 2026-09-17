import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearTokens,
  fetchCurrentUser,
  getAccessToken,
  getRefreshToken,
  login,
  logout,
  refreshAccessToken,
  registerAccount,
  storeTokens,
} from "@/lib/auth";
import type { TokenPair, User } from "@/lib/types";

const tokens: TokenPair = {
  access_token: "access-1",
  refresh_token: "refresh-1",
  token_type: "bearer",
};

const currentUser: User = {
  id: "user_1",
  email: "someone@example.com",
  full_name: "Someone",
  is_active: true,
  is_verified: true,
  role: "member",
};

/** Queues one successful JSON response per argument, in call order. */
function mockFetch(...bodies: unknown[]) {
  const fetchMock = vi.fn();
  for (const body of bodies) {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => body,
    });
  }
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("token storage", () => {
  it("round-trips the access token", () => {
    storeTokens(tokens);

    expect(getAccessToken()).toBe("access-1");
  });

  it("returns null when nothing is stored", () => {
    expect(getAccessToken()).toBeNull();
  });

  it("clears both tokens, not just the access token", () => {
    storeTokens(tokens);

    clearTokens();

    expect(getAccessToken()).toBeNull();
    expect(localStorage.getItem("saas.refresh_token")).toBeNull();
  });

  it("logout drops the stored session", () => {
    storeTokens(tokens);

    logout();

    expect(getAccessToken()).toBeNull();
  });
});

describe("registerAccount", () => {
  it("sends an explicit null full name when none is given", async () => {
    const fetchMock = mockFetch(currentUser);

    await registerAccount("someone@example.com", "hunter2hunter2");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/auth/register");
    expect(JSON.parse(init.body)).toEqual({
      email: "someone@example.com",
      password: "hunter2hunter2",
      full_name: null,
    });
  });

  it("passes the full name through when given", async () => {
    const fetchMock = mockFetch(currentUser);

    await registerAccount("someone@example.com", "hunter2hunter2", "Someone");

    expect(JSON.parse(fetchMock.mock.calls[0][1].body).full_name).toBe("Someone");
  });
});

describe("login", () => {
  it("stores the returned tokens and resolves the current user", async () => {
    const fetchMock = mockFetch(tokens, currentUser);

    const result = await login("someone@example.com", "hunter2hunter2");

    expect(result).toEqual(currentUser);
    expect(getAccessToken()).toBe("access-1");
    // The follow-up /users/me call has to carry the token that was just
    // stored, otherwise login would succeed but leave the session anonymous.
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBe("Bearer access-1");
  });
});

describe("fetchCurrentUser", () => {
  it("authorizes with the stored access token", async () => {
    storeTokens(tokens);
    const fetchMock = mockFetch(currentUser);

    await fetchCurrentUser();

    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer access-1");
  });
});

describe("refreshAccessToken", () => {
  it("redeems the stored refresh token and stores the new pair", async () => {
    storeTokens(tokens);
    const newTokens: TokenPair = {
      access_token: "access-2",
      refresh_token: "refresh-2",
      token_type: "bearer",
    };
    const fetchMock = mockFetch(newTokens);

    const result = await refreshAccessToken();

    expect(result).toBe("access-2");
    expect(getAccessToken()).toBe("access-2");
    // The rotated refresh token is what has to be kept: redeeming the old
    // one again would fail, since the backend retires it on rotation.
    expect(getRefreshToken()).toBe("refresh-2");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/auth/refresh");
    expect(JSON.parse(init.body)).toEqual({ refresh_token: "refresh-1" });
  });

  it("returns null without calling the server when there is no refresh token", async () => {
    const fetchMock = mockFetch();

    const result = await refreshAccessToken();

    expect(result).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("clears both tokens when the server refuses the refresh token", async () => {
    storeTokens(tokens);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        statusText: "Unauthorized",
        json: async () => ({ detail: "Invalid refresh token" }),
      })
    );

    const result = await refreshAccessToken();

    expect(result).toBeNull();
    // A refresh token that didn't work is not worth holding on to — the
    // account is signed out, not left retrying the same dead token.
    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
  });
});

describe("logout", () => {
  it("ends the session on the server, not just in this browser", async () => {
    storeTokens(tokens);
    const fetchMock = mockFetch({ detail: "Signed out" });

    await logout();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/auth/logout");
    // The refresh token is the credential being revoked, so it has to be
    // what goes up — an access token would leave it working for weeks.
    expect(JSON.parse(init.body)).toEqual({ refresh_token: "refresh-1" });
  });

  it("clears local tokens even when the request fails", async () => {
    storeTokens(tokens);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("network down"))
    );

    await logout();

    // Pressing "Log out" on a flaky connection must not leave someone
    // looking at a signed-in dashboard.
    expect(getAccessToken()).toBeNull();
    expect(localStorage.getItem("saas.refresh_token")).toBeNull();
  });

  it("skips the request when there is nothing to revoke", async () => {
    const fetchMock = mockFetch({ detail: "Signed out" });

    await logout();

    expect(fetchMock).not.toHaveBeenCalled();
  });
});

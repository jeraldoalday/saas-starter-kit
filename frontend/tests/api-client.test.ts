import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClientError, apiRequest, registerTokenRefresh } from "@/lib/api-client";

function mockFetchOnce(response: Partial<Response> & { json?: () => Promise<unknown> }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: "OK",
    json: async () => ({}),
    ...response,
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Queues one response per argument, in call order. */
function mockFetchSequence(
  ...responses: (Partial<Response> & { json?: () => Promise<unknown> })[]
) {
  const fetchMock = vi.fn();
  for (const response of responses) {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({}),
      ...response,
    });
  }
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  // Every test starts from "no refresh handler wired up", which is the
  // production default until main.tsx registers one — a test that forgets
  // to set its own would otherwise silently inherit the previous test's.
  registerTokenRefresh(null);
});

describe("apiRequest", () => {
  it("returns parsed JSON on a successful response", async () => {
    mockFetchOnce({ json: async () => ({ status: "ok" }) });

    const result = await apiRequest<{ status: string }>("/health");

    expect(result).toEqual({ status: "ok" });
  });

  it("sends a JSON body and Authorization header when provided", async () => {
    const fetchMock = mockFetchOnce({ json: async () => ({}) });

    await apiRequest("/users/me", { method: "POST", body: { foo: "bar" }, token: "abc123" });

    const [, init] = fetchMock.mock.calls[0];
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ foo: "bar" }));
    expect(init.headers.Authorization).toBe("Bearer abc123");
    expect(init.headers["Content-Type"]).toBe("application/json");
  });

  it("returns undefined for a 204 No Content response", async () => {
    mockFetchOnce({ status: 204 });

    const result = await apiRequest("/billing/webhook", { method: "POST" });

    expect(result).toBeUndefined();
  });

  it("throws ApiClientError with the server-provided detail on failure", async () => {
    mockFetchOnce({ ok: false, status: 401, json: async () => ({ detail: "Unauthorized" }) });

    await expect(apiRequest("/users/me")).rejects.toMatchObject(
      new ApiClientError(401, "Unauthorized")
    );
  });

  it("does not attempt a refresh on 401 when no handler is registered", async () => {
    // The production default before main.tsx wires one up, and the state
    // every other suite in this codebase already assumes 401 means "throw".
    const fetchMock = mockFetchOnce({
      ok: false,
      status: 401,
      json: async () => ({ detail: "Unauthorized" }),
    });

    await expect(apiRequest("/users/me", { token: "expired" })).rejects.toThrow();

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not refresh a request that carried no token", async () => {
    // An anonymous call (login, register) has nothing to refresh; refusing
    // it is not a session expiring.
    const refreshHandler = vi.fn().mockResolvedValue("new-access-token");
    registerTokenRefresh(refreshHandler);
    const fetchMock = mockFetchOnce({
      ok: false,
      status: 401,
      json: async () => ({ detail: "Unauthorized" }),
    });

    await expect(apiRequest("/auth/login", { method: "POST" })).rejects.toThrow();

    expect(refreshHandler).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("retries once with a new token when a 401 comes back and refreshing succeeds", async () => {
    registerTokenRefresh(vi.fn().mockResolvedValue("new-access-token"));
    const fetchMock = mockFetchSequence(
      { ok: false, status: 401, json: async () => ({ detail: "Unauthorized" }) },
      { json: async () => ({ id: "user_1" }) }
    );

    const result = await apiRequest<{ id: string }>("/users/me", { token: "stale" });

    expect(result).toEqual({ id: "user_1" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    // The retry carries the token the refresh handed back, not the stale one
    // that just failed — otherwise this would be a retry that fails the
    // same way forever.
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBe("Bearer new-access-token");
  });

  it("surfaces the original 401 when refreshing fails, without retrying again", async () => {
    registerTokenRefresh(vi.fn().mockResolvedValue(null));
    const fetchMock = mockFetchOnce({
      ok: false,
      status: 401,
      json: async () => ({ detail: "Unauthorized" }),
    });

    await expect(apiRequest("/users/me", { token: "stale" })).rejects.toMatchObject(
      new ApiClientError(401, "Unauthorized")
    );
    // One call, not a retry-of-the-retry: a refresh that failed once is not
    // going to succeed against the same dead refresh token a moment later.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shares one in-flight refresh across requests that 401 at the same time", async () => {
    let resolveRefresh: (token: string) => void = () => {};
    const refreshHandler = vi.fn(
      () => new Promise<string | null>((resolve) => (resolveRefresh = resolve))
    );
    registerTokenRefresh(refreshHandler);

    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      statusText: "Unauthorized",
      json: async () => ({ detail: "Unauthorized" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    // Two calls race in with a token that is about to turn out to be stale.
    const first = apiRequest("/users/me", { token: "stale" }).catch((e) => e);
    const second = apiRequest("/plan", { token: "stale" }).catch((e) => e);

    // Let both requests reach the refresh call before it resolves.
    await Promise.resolve();
    await Promise.resolve();
    resolveRefresh("new-access-token");
    await Promise.all([first, second]);

    expect(refreshHandler).toHaveBeenCalledTimes(1);
  });
});

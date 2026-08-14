import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClientError, apiRequest } from "@/lib/api-client";

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

afterEach(() => {
  vi.unstubAllGlobals();
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
});

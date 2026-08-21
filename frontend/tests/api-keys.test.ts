import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createApiKey,
  fetchApiKeys,
  formatRelativeTime,
  isKeyUsable,
  keyStatusLabel,
  revokeApiKey,
  sortKeys,
} from "@/lib/api-keys";
import type { ApiKey } from "@/lib/types";

const NOW = new Date("2026-08-21T12:00:00Z");

function key(overrides: Partial<ApiKey> = {}): ApiKey {
  return {
    id: "key_1",
    name: "CI deploy",
    prefix: "sk_live_ab12",
    created_at: "2026-08-01T12:00:00Z",
    last_used_at: null,
    expires_at: null,
    revoked_at: null,
    is_active: true,
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "OK",
    json: async () => body,
  } as Response;
}

describe("isKeyUsable", () => {
  it("is true for a key with no expiry", () => {
    expect(isKeyUsable(key(), NOW)).toBe(true);
  });

  it("is false once revoked", () => {
    expect(isKeyUsable(key({ revoked_at: "2026-08-10T00:00:00Z" }), NOW)).toBe(false);
  });

  it("is false past the expiry", () => {
    expect(isKeyUsable(key({ expires_at: "2026-08-20T00:00:00Z" }), NOW)).toBe(false);
  });

  it("is true before the expiry", () => {
    expect(isKeyUsable(key({ expires_at: "2026-09-01T00:00:00Z" }), NOW)).toBe(true);
  });

  it("does not trust a stale is_active from the server", () => {
    // The page was loaded before the key expired; the flag the API sent is
    // now wrong, and the recomputed answer has to win.
    const stale = key({ expires_at: "2026-08-20T00:00:00Z", is_active: true });

    expect(isKeyUsable(stale, NOW)).toBe(false);
  });

  it("treats an unparseable expiry as no opinion", () => {
    expect(isKeyUsable(key({ expires_at: "not-a-date" }), NOW)).toBe(true);
  });
});

describe("keyStatusLabel", () => {
  it.each([
    [key(), "Active"],
    [key({ revoked_at: "2026-08-10T00:00:00Z" }), "Revoked"],
    [key({ expires_at: "2026-08-01T00:00:00Z" }), "Expired"],
  ])("labels %o as %s", (input, expected) => {
    expect(keyStatusLabel(input as ApiKey, NOW)).toBe(expected);
  });

  it("prefers revoked over expired", () => {
    const both = key({
      revoked_at: "2026-08-02T00:00:00Z",
      expires_at: "2026-08-01T00:00:00Z",
    });

    expect(keyStatusLabel(both, NOW)).toBe("Revoked");
  });
});

describe("formatRelativeTime", () => {
  it("reports a key that has never been used", () => {
    expect(formatRelativeTime(null, NOW)).toBe("Never");
  });

  it.each([
    ["2026-08-21T11:59:30Z", "Just now"],
    ["2026-08-21T11:30:00Z", "30m ago"],
    ["2026-08-21T06:00:00Z", "6h ago"],
    ["2026-08-19T12:00:00Z", "2d ago"],
  ])("formats %s as %s", (timestamp, expected) => {
    expect(formatRelativeTime(timestamp, NOW)).toBe(expected);
  });

  it("falls back to a date beyond a week", () => {
    const formatted = formatRelativeTime("2026-07-01T12:00:00Z", NOW);

    expect(formatted).not.toMatch(/ago/);
    expect(formatted).toContain("2026");
  });

  it("reports an unparseable timestamp rather than NaN", () => {
    expect(formatRelativeTime("nonsense", NOW)).toBe("Unknown");
  });
});

describe("sortKeys", () => {
  it("puts usable keys first, then newest first", () => {
    const sorted = sortKeys(
      [
        key({ id: "old", created_at: "2026-01-01T00:00:00Z" }),
        key({ id: "revoked", revoked_at: "2026-08-02T00:00:00Z" }),
        key({ id: "new", created_at: "2026-08-15T00:00:00Z" }),
      ],
      NOW
    );

    expect(sorted.map((entry) => entry.id)).toEqual(["new", "old", "revoked"]);
  });

  it("does not mutate its input", () => {
    const input = [key({ id: "a" }), key({ id: "b", revoked_at: "2026-08-02T00:00:00Z" })];
    const before = input.map((entry) => entry.id);

    sortKeys(input, NOW);

    expect(input.map((entry) => entry.id)).toEqual(before);
  });
});

describe("requests", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    localStorage.setItem("saas.access_token", "session-token");
  });

  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("lists keys with the session token", async () => {
    fetchMock.mockResolvedValue(jsonResponse([key()]));

    const keys = await fetchApiKeys();

    expect(keys).toHaveLength(1);
    const [, options] = fetchMock.mock.calls[0];
    expect(options.headers.Authorization).toBe("Bearer session-token");
  });

  it("sends the name and expiry when creating", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ...key(), key: "sk_live_secret" }, 201));

    const created = await createApiKey("CI deploy", 30);

    const [, options] = fetchMock.mock.calls[0];
    expect(JSON.parse(options.body)).toEqual({
      name: "CI deploy",
      expires_in_days: 30,
    });
    expect(created.key).toBe("sk_live_secret");
  });

  it("defaults to a key that does not expire", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ...key(), key: "sk_live_secret" }, 201));

    await createApiKey("Forever");

    const [, options] = fetchMock.mock.calls[0];
    expect(JSON.parse(options.body).expires_in_days).toBeNull();
  });

  it("revokes by id", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 204 } as Response);

    await revokeApiKey("key_1");

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/api-keys/key_1");
    expect(options.method).toBe("DELETE");
  });

  it("surfaces an API error rather than swallowing it", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ detail: "At most 20 active keys per account" }, 409)
    );

    await expect(createApiKey("One too many")).rejects.toThrow(
      "At most 20 active keys per account"
    );
  });
});

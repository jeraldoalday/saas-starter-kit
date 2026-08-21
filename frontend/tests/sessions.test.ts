import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { storeTokens } from "@/lib/auth";
import {
  describeUserAgent,
  fetchSessions,
  isSessionActive,
  revokeAllSessions,
  revokeSession,
  sessionStatusLabel,
  sortSessions,
} from "@/lib/sessions";
import type { AuthSession, TokenPair } from "@/lib/types";

const tokens: TokenPair = {
  access_token: "access-1",
  refresh_token: "refresh-1",
  token_type: "bearer",
};

const NOW = new Date("2026-08-22T12:00:00Z");

function session(overrides: Partial<AuthSession> = {}): AuthSession {
  return {
    id: "session_1",
    created_at: "2026-08-01T12:00:00Z",
    last_used_at: "2026-08-22T09:00:00Z",
    expires_at: "2026-09-05T12:00:00Z",
    revoked_at: null,
    user_agent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/126.0.0.0 Safari/537.36",
    ip_address: "203.0.113.7",
    is_active: true,
    ...overrides,
  };
}

function mockFetch(body: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    statusText: "OK",
    json: async () => body,
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  localStorage.clear();
  storeTokens(tokens);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchSessions", () => {
  it("authorizes with the access token, never an API key", async () => {
    const fetchMock = mockFetch([session()]);

    await fetchSessions();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/sessions");
    expect(init.headers.Authorization).toBe("Bearer access-1");
    expect(init.headers["X-API-Key"]).toBeUndefined();
  });
});

describe("revokeSession", () => {
  it("deletes the identified session", async () => {
    const fetchMock = mockFetch(undefined, 204);

    await revokeSession("session_9");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/sessions/session_9");
    expect(init.method).toBe("DELETE");
  });
});

describe("revokeAllSessions", () => {
  it("sends this browser's refresh token when keeping the current session", async () => {
    const fetchMock = mockFetch({ revoked: 2 });

    await revokeAllSessions(true);

    // The API cannot identify the caller's session from an access token, so
    // sparing this browser depends entirely on sending the refresh token.
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      keep_current_refresh_token: "refresh-1",
    });
  });

  it("sends null when signing out everywhere including here", async () => {
    const fetchMock = mockFetch({ revoked: 3 });

    await revokeAllSessions(false);

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      keep_current_refresh_token: null,
    });
  });

  it("returns how many sessions were ended", async () => {
    mockFetch({ revoked: 4 });

    expect(await revokeAllSessions(false)).toEqual({ revoked: 4 });
  });
});

describe("isSessionActive", () => {
  it("treats a revoked session as inactive however far off its expiry is", () => {
    expect(
      isSessionActive(session({ revoked_at: "2026-08-20T12:00:00Z" }), NOW)
    ).toBe(false);
  });

  it("treats a lapsed expiry as inactive even when the API said otherwise", () => {
    // The API's is_active was computed when the page loaded; this is what
    // stops an overnight tab showing a dead session as live.
    expect(
      isSessionActive(session({ expires_at: "2026-08-21T12:00:00Z" }), NOW)
    ).toBe(false);
  });

  it("keeps a live session active", () => {
    expect(isSessionActive(session(), NOW)).toBe(true);
  });

  it("shows a session with an unparseable expiry rather than hiding it", () => {
    // Hiding a live session is the worse failure: the page exists to show
    // every device that can reach the account.
    expect(isSessionActive(session({ expires_at: "not-a-date" }), NOW)).toBe(true);
  });
});

describe("sessionStatusLabel", () => {
  it("distinguishes being signed out from timing out", () => {
    expect(sessionStatusLabel(session({ revoked_at: "2026-08-20T12:00:00Z" }), NOW)).toBe(
      "Signed out"
    );
    expect(sessionStatusLabel(session({ expires_at: "2026-08-01T12:00:00Z" }), NOW)).toBe(
      "Expired"
    );
    expect(sessionStatusLabel(session(), NOW)).toBe("Active");
  });
});

describe("describeUserAgent", () => {
  it.each([
    [
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
      "Chrome on macOS",
    ],
    [
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Version/17.5 Safari/604.1",
      "Safari on iPhone",
    ],
    [
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
      "Edge on Windows",
    ],
    ["Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/127.0", "Firefox on Linux"],
    [
      "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/126.0.0.0 Mobile Safari/537.36",
      "Chrome on Android",
    ],
  ])("describes %s", (userAgent, expected) => {
    expect(describeUserAgent(userAgent)).toBe(expected);
  });

  it("does not let Chrome's Safari token win over Edge", () => {
    // Every Chromium browser also claims Safari and Chrome, so ordering is
    // the whole implementation here.
    const edge =
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36 Edg/126";
    expect(describeUserAgent(edge)).toBe("Edge on macOS");
  });

  it("falls back to the raw string for a non-browser caller", () => {
    // A caller that isn't a browser usually names itself more usefully than
    // any label this could invent.
    expect(describeUserAgent("curl/8.4.0")).toBe("curl/8.4.0");
  });

  it("truncates an unrecognised string rather than breaking the layout", () => {
    const long = "x".repeat(120);

    const described = describeUserAgent(long);

    expect(described).toHaveLength(41);
    expect(described.endsWith("…")).toBe(true);
  });

  it("handles a missing or blank user agent", () => {
    expect(describeUserAgent(null)).toBe("Unknown device");
    expect(describeUserAgent("   ")).toBe("Unknown device");
  });
});

describe("sortSessions", () => {
  it("puts live sessions above dead ones", () => {
    const dead = session({ id: "dead", revoked_at: "2026-08-21T12:00:00Z" });
    const live = session({ id: "live" });

    expect(sortSessions([dead, live], NOW).map((s) => s.id)).toEqual(["live", "dead"]);
  });

  it("orders live sessions by most recently used", () => {
    const stale = session({ id: "stale", last_used_at: "2026-08-10T12:00:00Z" });
    const recent = session({ id: "recent", last_used_at: "2026-08-22T11:00:00Z" });

    expect(sortSessions([stale, recent], NOW).map((s) => s.id)).toEqual([
      "recent",
      "stale",
    ]);
  });

  it("does not mutate the array it was given", () => {
    const input = [session({ id: "a" }), session({ id: "b", revoked_at: "2026-08-01T00:00:00Z" })];

    sortSessions(input, NOW);

    expect(input.map((s) => s.id)).toEqual(["a", "b"]);
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  auditTone,
  describeAuditAction,
  describeAuditContext,
  fetchAuditPage,
  hasMorePages,
  isSecurityCritical,
  PAGE_SIZE,
  securityHighlights,
} from "@/lib/audit";
import { storeTokens } from "@/lib/auth";
import type { AuditEntry, AuditPage, TokenPair } from "@/lib/types";

const tokens: TokenPair = {
  access_token: "access-1",
  refresh_token: "refresh-1",
  token_type: "bearer",
};

function entry(overrides: Partial<AuditEntry> = {}): AuditEntry {
  return {
    id: "audit_1",
    action: "auth.login",
    outcome: "success",
    ip_address: "203.0.113.7",
    user_agent: "Mozilla/5.0",
    context: {},
    created_at: "2026-08-28T09:00:00Z",
    by_another_account: false,
    ...overrides,
  };
}

function page(overrides: Partial<AuditPage> = {}): AuditPage {
  return { entries: [entry()], total: 1, limit: PAGE_SIZE, offset: 0, ...overrides };
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

describe("fetchAuditPage", () => {
  beforeEach(() => {
    localStorage.clear();
    storeTokens(tokens);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("asks for the first page by default", async () => {
    const fetchMock = mockFetch(page());

    await fetchAuditPage();

    expect(fetchMock.mock.calls[0][0]).toContain(`limit=${PAGE_SIZE}&offset=0`);
  });

  it("sends the session token, never an API key", async () => {
    const fetchMock = mockFetch(page());

    await fetchAuditPage();

    // The API refuses key-authenticated requests here on purpose: this
    // history is a list of the addresses someone signs in from.
    const { headers } = fetchMock.mock.calls[0][1];
    expect(headers.Authorization).toBe("Bearer access-1");
    expect(headers["X-API-Key"]).toBeUndefined();
  });

  it("passes the offset through for later pages", async () => {
    const fetchMock = mockFetch(page({ offset: 50 }));

    await fetchAuditPage(50);

    expect(fetchMock.mock.calls[0][0]).toContain("offset=50");
  });
});

describe("describeAuditAction", () => {
  it("reads as the account holder's own history", () => {
    expect(describeAuditAction("auth.login")).toBe("Signed in");
    expect(describeAuditAction("auth.login_failed")).toBe(
      "Failed sign-in attempt"
    );
  });

  it("shows an unknown action rather than hiding it", () => {
    // A backend that has shipped a new action should still produce a
    // readable-ish row; an invisible row is worse than an ugly one on a
    // security page.
    expect(describeAuditAction("auth.something_new")).toBe("auth.something_new");
  });

  it("has wording for every action the API can send", () => {
    const actions = [
      "auth.login",
      "auth.login_failed",
      "auth.login_blocked",
      "auth.logout",
      "auth.password_reset_requested",
      "auth.password_reset_completed",
      "auth.email_verified",
      "account.registered",
      "account.deleted",
      "account.exported",
      "account.role_changed",
      "account.status_changed",
      "api_key.created",
      "api_key.revoked",
      "session.revoked",
      "session.revoked_all",
    ];

    for (const action of actions) {
      expect(describeAuditAction(action)).not.toBe(action);
    }
  });
});

describe("auditTone", () => {
  it("makes a refusal the loudest thing on the page", () => {
    expect(auditTone(entry({ outcome: "failure" }))).toBe("alert");
  });

  it("marks an action somebody else took as notable", () => {
    expect(auditTone(entry({ by_another_account: true }))).toBe("notable");
  });

  it("leaves ordinary product use unstyled", () => {
    // Colouring everything would mean nothing stands out.
    expect(auditTone(entry())).toBe("normal");
  });
});

describe("describeAuditContext", () => {
  it("names the key a row is about", () => {
    expect(
      describeAuditContext(entry({ context: { prefix: "sk_live_8f2a" } }))
    ).toBe("Key sk_live_8f2a…");
  });

  it("shows a role change as a transition", () => {
    expect(
      describeAuditContext(entry({ context: { from: "member", to: "admin" } }))
    ).toBe("member → admin");
  });

  it("counts the devices a bulk sign-out ended", () => {
    expect(describeAuditContext(entry({ context: { revoked: 1 } }))).toBe(
      "1 device"
    );
    expect(describeAuditContext(entry({ context: { revoked: 4 } }))).toBe(
      "4 devices"
    );
  });

  it("says nothing when there is nothing worth saying", () => {
    expect(describeAuditContext(entry())).toBeNull();
  });

  it("does not surface known_account", () => {
    // To the owner of an account a failed sign-in is always against a known
    // account, so the flag reads as noise on every row that carries it.
    expect(
      describeAuditContext(
        entry({ action: "auth.login_failed", context: { known_account: true } })
      )
    ).toBeNull();
  });

  it("returns one clause at most", () => {
    const detail = describeAuditContext(
      entry({ context: { prefix: "sk_live_8f2a", name: "CI deploy" } })
    );

    // A column that sometimes holds four facts and sometimes none is
    // unreadable.
    expect(detail).toBe("Key sk_live_8f2a…");
  });
});

describe("hasMorePages", () => {
  it("knows when the first page is the only one", () => {
    expect(hasMorePages(page({ total: 1 }))).toBe(false);
  });

  it("knows when more remain", () => {
    expect(hasMorePages(page({ total: 40 }))).toBe(true);
  });

  it("counts from the offset, not from zero", () => {
    expect(
      hasMorePages(page({ entries: [entry()], offset: 39, total: 40 }))
    ).toBe(false);
  });
});

describe("securityHighlights", () => {
  it("keeps only what the account holder did not do themselves", () => {
    const entries = [
      entry({ id: "a", action: "auth.login" }),
      entry({ id: "b", action: "auth.login_failed", outcome: "failure" }),
      entry({ id: "c", action: "api_key.created" }),
      entry({ id: "d", action: "account.role_changed", by_another_account: true }),
    ];

    expect(securityHighlights(entries).map((row) => row.id)).toEqual(["b", "d"]);
  });

  it("stops at the limit", () => {
    const entries = Array.from({ length: 6 }, (_, index) =>
      entry({ id: `row-${index}`, outcome: "failure" })
    );

    expect(securityHighlights(entries)).toHaveLength(3);
  });

  it("treats anything another account did as worth surfacing", () => {
    expect(
      isSecurityCritical(entry({ action: "auth.logout", by_another_account: true }))
    ).toBe(true);
  });
});

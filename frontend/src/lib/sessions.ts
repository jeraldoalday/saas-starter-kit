import { apiRequest } from "@/lib/api-client";
import { getAccessToken, getRefreshToken } from "@/lib/auth";
import type { AuthSession } from "@/lib/types";

/**
 * Session management — the devices currently signed in to this account.
 *
 * Every call goes out with the access token rather than with an API key: the
 * API refuses key-authenticated requests on these routes on purpose, so a
 * leaked key cannot sign the real owner out and keep the account to itself.
 */

export function fetchSessions(): Promise<AuthSession[]> {
  return apiRequest<AuthSession[]>("/sessions", { token: getAccessToken() });
}

export function revokeSession(sessionId: string): Promise<void> {
  return apiRequest<void>(`/sessions/${sessionId}`, {
    method: "DELETE",
    token: getAccessToken(),
  });
}

/**
 * Sign out everywhere.
 *
 * `keepCurrent` decides whether this browser survives. The API cannot work
 * that out on its own — an access token carries no session identity — so the
 * refresh token this browser holds is what identifies the session to spare.
 */
export function revokeAllSessions(keepCurrent: boolean): Promise<{ revoked: number }> {
  return apiRequest<{ revoked: number }>("/sessions/revoke-all", {
    method: "POST",
    body: {
      keep_current_refresh_token: keepCurrent ? getRefreshToken() : null,
    },
    token: getAccessToken(),
  });
}

/**
 * Whether a session could still refresh right now.
 *
 * Recomputed on the client rather than trusting the `is_active` the API
 * sent, for the reason the API key list gives: a page left open overnight
 * would otherwise keep showing a session that expired at 3am as live.
 */
export function isSessionActive(session: AuthSession, now: Date = new Date()): boolean {
  if (session.revoked_at !== null) return false;

  const expiry = new Date(session.expires_at);
  // An unparseable timestamp is treated as "no opinion" rather than as
  // expired: hiding a live session is the worse failure, because the whole
  // point of the page is showing someone every device that can reach their
  // account.
  if (Number.isNaN(expiry.getTime())) return true;
  return expiry.getTime() > now.getTime();
}

/** Why a session is not usable, for the status column. */
export function sessionStatusLabel(
  session: AuthSession,
  now: Date = new Date()
): string {
  if (session.revoked_at !== null) return "Signed out";
  if (!isSessionActive(session, now)) return "Expired";
  return "Active";
}

/** Browsers, longest-token-first so "Edg" is not swallowed by "Chrome". */
const BROWSERS: ReadonlyArray<readonly [RegExp, string]> = [
  [/\bEdg[A-Za-z]*\//, "Edge"],
  [/\bOPR\//, "Opera"],
  [/\bFirefox\//, "Firefox"],
  [/\bChrome\//, "Chrome"],
  // Safari last: every WebKit browser above also claims it.
  [/\bSafari\//, "Safari"],
];

const PLATFORMS: ReadonlyArray<readonly [RegExp, string]> = [
  [/\biPhone\b/, "iPhone"],
  [/\biPad\b/, "iPad"],
  [/\bAndroid\b/, "Android"],
  [/\bMac OS X\b|\bMacintosh\b/, "macOS"],
  [/\bWindows\b/, "Windows"],
  [/\bCrOS\b/, "ChromeOS"],
  [/\bLinux\b/, "Linux"],
];

/**
 * "Chrome on macOS" from a User-Agent string.
 *
 * Deliberately shallow. A User-Agent is self-reported and unverifiable, and
 * this text exists for exactly one job: helping someone recognise which row
 * is the laptop in front of them. Getting it slightly wrong costs nothing,
 * so an unrecognised string is shown as-is (truncated) rather than guessed
 * at — a caller that isn't a browser is usually something whose own name is
 * more informative than any label this could invent.
 */
export function describeUserAgent(userAgent: string | null): string {
  if (userAgent === null || userAgent.trim() === "") return "Unknown device";

  const browser = BROWSERS.find(([pattern]) => pattern.test(userAgent))?.[1];
  const platform = PLATFORMS.find(([pattern]) => pattern.test(userAgent))?.[1];

  if (browser && platform) return `${browser} on ${platform}`;
  if (browser) return browser;
  if (platform) return platform;

  const trimmed = userAgent.trim();
  return trimmed.length > 40 ? `${trimmed.slice(0, 40)}…` : trimmed;
}

/**
 * Sort order for the table: live sessions first, then most recently used.
 *
 * last_used_at rather than created_at, because the question the list answers
 * is "what is reaching my account", and a session created months ago that
 * refreshed this morning is more interesting than yesterday's idle one.
 */
export function sortSessions(
  sessions: AuthSession[],
  now: Date = new Date()
): AuthSession[] {
  return [...sessions].sort((left, right) => {
    const active =
      Number(isSessionActive(right, now)) - Number(isSessionActive(left, now));
    if (active !== 0) return active;
    return (
      new Date(right.last_used_at).getTime() - new Date(left.last_used_at).getTime()
    );
  });
}

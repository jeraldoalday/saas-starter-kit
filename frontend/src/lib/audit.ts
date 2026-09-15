import { apiRequest } from "@/lib/api-client";
import { getAccessToken } from "@/lib/auth";
import type { AuditEntry, AuditPage } from "@/lib/types";

/**
 * The account's own security trail.
 *
 * Like sessions and API keys, every call goes out with the access token
 * rather than with an API key: the API refuses key-authenticated requests on
 * these routes on purpose, because this history is a list of the addresses
 * someone signs in from and a map of when they are not watching.
 */

/** How many entries one page asks for. Matches the API's own default. */
export const PAGE_SIZE = 25;

export function fetchAuditPage(
  offset = 0,
  limit: number = PAGE_SIZE
): Promise<AuditPage> {
  return apiRequest<AuditPage>(
    `/audit/me?limit=${limit}&offset=${offset}`,
    { token: getAccessToken() }
  );
}

/**
 * Plain-English label for a dotted action name.
 *
 * A lookup rather than a transformation of the string, because the wording
 * is the whole product here: someone scanning this page is deciding whether
 * to change their password, and "auth.login_failed" does not help them do
 * that. Phrased from the account holder's point of view throughout — it is
 * their history, not the system's log.
 */
const ACTION_LABELS: Readonly<Record<string, string>> = {
  "auth.login": "Signed in",
  "auth.login_failed": "Failed sign-in attempt",
  "auth.login_blocked": "Sign-in blocked — account deactivated",
  "auth.logout": "Signed out",
  "auth.password_reset_requested": "Password reset requested",
  "auth.password_reset_completed": "Password changed",
  "auth.email_verified": "Email address verified",
  "account.registered": "Account created",
  "account.deleted": "Account deleted",
  "account.exported": "Account data exported",
  "account.role_changed": "Role changed",
  "account.status_changed": "Account status changed",
  "api_key.created": "API key created",
  "api_key.revoked": "API key revoked",
  "session.revoked": "Device signed out",
  "session.revoked_all": "All devices signed out",
  "webhook.endpoint_created": "Webhook endpoint added",
  "webhook.endpoint_updated": "Webhook endpoint updated",
  "webhook.endpoint_deleted": "Webhook endpoint deleted",
  "webhook.secret_rotated": "Webhook signing secret rotated",
  "webhook.secret_revealed": "Webhook signing secret revealed",
};

export function describeAuditAction(action: string): string {
  // An action this build has no wording for is shown as its raw name rather
  // than hidden or labelled "Unknown". A backend that has shipped a new
  // action should still produce a readable-ish row here, and a row nobody
  // can see is worse than an ugly one — this is a security page.
  return ACTION_LABELS[action] ?? action;
}

/**
 * How much attention a row deserves, for styling.
 *
 * Three levels rather than a boolean. A refusal is the loudest thing on the
 * page; an action somebody else took against the account is quieter but
 * still not routine; everything else is the ordinary noise of using the
 * product, and colouring all of it would mean none of it stands out.
 */
export type AuditTone = "alert" | "notable" | "normal";

export function auditTone(entry: AuditEntry): AuditTone {
  if (entry.outcome === "failure") return "alert";
  if (entry.by_another_account) return "notable";
  return "normal";
}

/** Actions worth surfacing on the dashboard without opening the full page. */
const SECURITY_CRITICAL_ACTIONS = new Set([
  "auth.login_failed",
  "auth.login_blocked",
  "auth.password_reset_completed",
  "account.role_changed",
  "account.status_changed",
]);

export function isSecurityCritical(entry: AuditEntry): boolean {
  // Any refusal counts, whatever it was refusing. Listing actions alone
  // would mean a new failure mode shipped on the backend silently stops
  // being surfaced here until somebody remembers to add its name.
  return (
    entry.outcome === "failure" ||
    entry.by_another_account ||
    SECURITY_CRITICAL_ACTIONS.has(entry.action)
  );
}

/**
 * The one extra detail a row carries, or null.
 *
 * Deliberately at most one clause. The context bag holds whatever the
 * backend found worth keeping, but a table column that sometimes contains
 * four facts and sometimes none is unreadable — and the details that matter
 * to a person reading their own history are few.
 */
export function describeAuditContext(entry: AuditEntry): string | null {
  const { context } = entry;

  if (typeof context.prefix === "string") return `Key ${context.prefix}…`;
  if (typeof context.name === "string") return `“${context.name}”`;
  if (typeof context.from === "string" && typeof context.to === "string") {
    return `${context.from} → ${context.to}`;
  }
  if (typeof context.is_active === "boolean") {
    return context.is_active ? "Reactivated" : "Deactivated";
  }
  if (typeof context.revoked === "number") {
    return `${context.revoked} device${context.revoked === 1 ? "" : "s"}`;
  }
  // known_account is deliberately not surfaced: to the owner of the account
  // a failed sign-in is always against a known account, so the flag would
  // read as noise on every row that carries it.
  return null;
}

/** Whether another page exists after the one just loaded. */
export function hasMorePages(page: AuditPage): boolean {
  return page.offset + page.entries.length < page.total;
}

/**
 * Rows the account holder did not cause themselves, newest first.
 *
 * The dashboard summary: a short list answering "has anything happened to my
 * account that I did not do", which is the question the full page is too
 * long to answer at a glance.
 */
export function securityHighlights(
  entries: AuditEntry[],
  limit = 3
): AuditEntry[] {
  return entries.filter(isSecurityCritical).slice(0, limit);
}

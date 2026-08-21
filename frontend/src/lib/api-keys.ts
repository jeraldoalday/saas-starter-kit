import { apiRequest } from "@/lib/api-client";
import { getAccessToken } from "@/lib/auth";
import type { ApiKey, CreatedApiKey } from "@/lib/types";

/**
 * API key management.
 *
 * Every call here goes out with the session token rather than with a key:
 * the API refuses key-authenticated requests on these routes on purpose, so
 * a leaked key cannot mint more of itself.
 */

/** Longest lifetime the API will accept, mirrored so the UI can say so. */
export const MAX_EXPIRY_DAYS = 365;

export function fetchApiKeys(): Promise<ApiKey[]> {
  return apiRequest<ApiKey[]>("/api-keys", { token: getAccessToken() });
}

/**
 * Mint a key. The `key` field of the response is the only time the secret
 * exists anywhere outside the caller's machine — it is not stored, so a
 * client that drops it has to create another.
 */
export function createApiKey(
  name: string,
  expiresInDays: number | null = null
): Promise<CreatedApiKey> {
  return apiRequest<CreatedApiKey>("/api-keys", {
    method: "POST",
    body: { name, expires_in_days: expiresInDays },
    token: getAccessToken(),
  });
}

export function revokeApiKey(keyId: string): Promise<void> {
  return apiRequest<void>(`/api-keys/${keyId}`, {
    method: "DELETE",
    token: getAccessToken(),
  });
}

/**
 * Whether a key would authenticate a request right now.
 *
 * Recomputed on the client rather than trusting the `is_active` the API
 * sent, because a page left open overnight would otherwise keep showing a
 * key that expired at midnight as active.
 */
export function isKeyUsable(key: ApiKey, now: Date = new Date()): boolean {
  if (key.revoked_at !== null) return false;
  if (key.expires_at === null) return true;

  const expiry = new Date(key.expires_at);
  // An unparseable timestamp is treated as "no opinion" rather than as
  // expired: refusing to show a working key is the worse failure.
  if (Number.isNaN(expiry.getTime())) return true;
  return expiry.getTime() > now.getTime();
}

/** Why a key is not usable, for the status column. */
export function keyStatusLabel(key: ApiKey, now: Date = new Date()): string {
  if (key.revoked_at !== null) return "Revoked";
  if (!isKeyUsable(key, now)) return "Expired";
  return "Active";
}

/**
 * "3 days ago", "just now", "never" — the resolution someone actually reads
 * a last-used column at. Deliberately coarse above a week, where an exact
 * date is more useful than a count of days.
 */
export function formatRelativeTime(
  timestamp: string | null,
  now: Date = new Date()
): string {
  if (timestamp === null) return "Never";

  const then = new Date(timestamp);
  if (Number.isNaN(then.getTime())) return "Unknown";

  const seconds = Math.floor((now.getTime() - then.getTime()) / 1000);
  if (seconds < 60) return "Just now";

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;

  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;

  return then.toLocaleDateString();
}

/** Sort order for the table: usable keys first, then newest first. */
export function sortKeys(keys: ApiKey[], now: Date = new Date()): ApiKey[] {
  return [...keys].sort((left, right) => {
    const usable = Number(isKeyUsable(right, now)) - Number(isKeyUsable(left, now));
    if (usable !== 0) return usable;
    return new Date(right.created_at).getTime() - new Date(left.created_at).getTime();
  });
}

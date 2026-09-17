import type { ApiError } from "@/lib/types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

export class ApiClientError extends Error {
  status: number;
  /**
   * Seconds the server asked the caller to wait, from Retry-After, or null.
   *
   * Carried on the error because 429 alone does not say how long: a rate
   * limiter means a minute and a spent monthly allowance means weeks, and a
   * client that cannot tell them apart either hammers the endpoint or gives
   * up on it for the session.
   */
  retryAfterSeconds: number | null;

  constructor(status: number, message: string, retryAfterSeconds: number | null = null) {
    super(message);
    this.status = status;
    this.retryAfterSeconds = retryAfterSeconds;
    this.name = "ApiClientError";
  }
}

/**
 * Retry-After as whole seconds, or null.
 *
 * Reads defensively: the header is absent on most responses, and a test
 * double or a proxy may hand back a response object without `headers` at
 * all. A missing or unparseable value is null, which callers read as "no
 * guidance" rather than as zero.
 */
function retryAfterSeconds(response: Response): number | null {
  const raw = response.headers?.get?.("Retry-After");
  if (!raw) return null;
  const seconds = Number(raw);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  token?: string | null;
}

/**
 * Redeems the stored refresh token for a new access token, or resolves to
 * null when there is nothing left to redeem it with.
 *
 * Registered once from app startup rather than imported directly: lib/auth.ts
 * already imports apiRequest, and a static import back from here would make
 * the two modules circular. Left unset (null) in tests, which is what keeps
 * every existing 401 expectation — thrown, not silently retried — true
 * without those tests knowing this exists.
 */
let refreshAccessToken: (() => Promise<string | null>) | null = null;

export function registerTokenRefresh(handler: (() => Promise<string | null>) | null): void {
  refreshAccessToken = handler;
}

// Concurrent requests that all outlive the access token would otherwise each
// kick off their own refresh; since the refresh token rotates on redemption,
// only the first would succeed and the rest would read a token already
// spent, signing the session out. Sharing one in-flight attempt is what
// makes "several requests fire the moment the token expires" behave like
// one refresh instead of a race.
let refreshInFlight: Promise<string | null> | null = null;

function refreshOnce(): Promise<string | null> {
  if (refreshAccessToken === null) {
    return Promise.resolve(null);
  }
  if (refreshInFlight === null) {
    refreshInFlight = refreshAccessToken().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

async function send<T>(
  path: string,
  method: string,
  body: unknown,
  token: string | null | undefined,
  canRefresh: boolean
): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  // Retried at most once, and only for a request that carried a token in
  // the first place — an already-anonymous call has nothing to refresh, and
  // retrying the retry would turn a dead refresh token into a loop.
  if (response.status === 401 && canRefresh && token) {
    const newToken = await refreshOnce();
    if (newToken) {
      return send<T>(path, method, body, newToken, false);
    }
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const data = await response.json().catch(() => undefined);

  if (!response.ok) {
    const message = (data as ApiError | undefined)?.detail ?? response.statusText;
    throw new ApiClientError(response.status, message, retryAfterSeconds(response));
  }

  return data as T;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, token } = options;
  return send<T>(path, method, body, token, true);
}

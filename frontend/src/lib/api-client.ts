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

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, token } = options;

  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

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

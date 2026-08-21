import { apiRequest } from "@/lib/api-client";
import type { TokenPair, User } from "@/lib/types";

const ACCESS_TOKEN_KEY = "saas.access_token";
const REFRESH_TOKEN_KEY = "saas.refresh_token";

export function storeTokens(tokens: TokenPair): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
  localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
}

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
}

export function registerAccount(
  email: string,
  password: string,
  fullName?: string
): Promise<User> {
  return apiRequest<User>("/auth/register", {
    method: "POST",
    body: { email, password, full_name: fullName ?? null },
  });
}

export async function login(email: string, password: string): Promise<User> {
  const tokens = await apiRequest<TokenPair>("/auth/login", {
    method: "POST",
    body: { email, password },
  });
  storeTokens(tokens);
  return fetchCurrentUser();
}

export function fetchCurrentUser(): Promise<User> {
  const token = getAccessToken();
  return apiRequest<User>("/users/me", { token });
}

/**
 * End the session, on the server as well as in this browser.
 *
 * Local tokens are cleared first and unconditionally, before the request is
 * even attempted. Signing out has to work on a flaky connection: if the call
 * failed and we had cleared nothing, the user would be left looking at a
 * signed-in dashboard having just pressed "Log out".
 *
 * The request itself is best-effort for the same reason. Its value is that
 * the refresh token stops working for anyone who captured it — worth doing,
 * not worth blocking the UI on, and the token expires on its own regardless.
 */
export async function logout(): Promise<void> {
  const refreshToken = getRefreshToken();
  clearTokens();

  if (refreshToken === null) return;

  try {
    await apiRequest<{ detail: string }>("/auth/logout", {
      method: "POST",
      body: { refresh_token: refreshToken },
    });
  } catch {
    // Already signed out locally; nothing here is worth surfacing.
  }
}

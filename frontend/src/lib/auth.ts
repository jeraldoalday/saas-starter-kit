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

export function logout(): void {
  clearTokens();
}

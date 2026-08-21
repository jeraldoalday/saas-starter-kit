export interface User {
  id: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  is_verified: boolean;
  /** "member" | "admin" | "owner" — ranked, so a view asks for a minimum. */
  role: string;
}

export interface ApiKey {
  id: string;
  name: string;
  /** Recognisable slice of the secret; enough to tell two keys apart. */
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  is_active: boolean;
}

/** The creation response — the only time the raw secret is ever returned. */
export interface CreatedApiKey extends ApiKey {
  key: string;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface Subscription {
  id: string;
  status: string;
  current_period_end: string | null;
}

export interface ApiError {
  detail: string;
}

/** Scalar property bag attached to a usage event. */
export type EventProperties = Record<string, string | number | boolean | null>;

export interface DailyCount {
  /** ISO date (YYYY-MM-DD) in UTC. */
  day: string;
  count: number;
}

export interface UsageSummary {
  window_days: number;
  total_events: number;
  active_users: number;
  events_by_name: Record<string, number>;
  daily: DailyCount[];
}

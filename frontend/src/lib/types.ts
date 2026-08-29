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

/** One signed-in device, as the account's own session list shows it. */
export interface AuthSession {
  id: string;
  created_at: string;
  last_used_at: string;
  expires_at: string;
  revoked_at: string | null;
  /** Self-reported and unverified; only ever used to help someone recognise a device. */
  user_agent: string | null;
  ip_address: string | null;
  is_active: boolean;
}

/**
 * One entry in the account's own security trail.
 *
 * Carries no identifier for whoever caused it — only whether it was somebody
 * else. The API draws that line deliberately: naming the administrator would
 * hand an internal identifier to a member with no claim to it.
 */
export interface AuditEntry {
  id: string;
  /** Dotted action name, e.g. "auth.login_failed". */
  action: string;
  /** "success" | "failure". */
  outcome: string;
  ip_address: string | null;
  /** Self-reported and unverified, exactly as on a session. */
  user_agent: string | null;
  context: Record<string, string | number | boolean | null>;
  created_at: string;
  by_another_account: boolean;
}

/** A page of trail, with the total behind it so a client can page. */
export interface AuditPage {
  entries: AuditEntry[];
  total: number;
  limit: number;
  offset: number;
  /**
   * How far back the account's plan lets it read, in days, or null on a
   * plan with no limit. Shown to the reader, because a trail that stops
   * after thirty days with no explanation reads as rows somebody deleted.
   */
  history_days: number | null;
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

/**
 * One plan's ceilings, exactly as the catalog defines them.
 *
 * Every limit is nullable, and null means *no ceiling* rather than unknown
 * or zero — so the UI renders "Unlimited" on a null and never has to know a
 * sentinel number.
 */
export interface Entitlements {
  plan_key: string;
  display_name: string;
  max_active_api_keys: number | null;
  max_active_sessions: number | null;
  monthly_event_allowance: number | null;
  audit_history_days: number | null;
}

/** One ceiling and how much of it this account has spent. */
export interface Quota {
  /** Stable identifier: "api_keys" | "sessions" | "events". */
  key: string;
  /** Human wording for the thing being counted, from the API. */
  label: string;
  limit: number | null;
  used: number;
  /** Served rather than subtracted here, so every view clamps alike. */
  remaining: number | null;
  exhausted: boolean;
}

/** The caller's plan, its ceilings, and what is left of each. */
export interface Plan {
  entitlements: Entitlements;
  /** The Stripe status behind the plan, or null if never subscribed. */
  subscription_status: string | null;
  quotas: Quota[];
  /** ISO timestamps bounding the monthly event allowance. */
  period_start: string;
  period_end: string;
}

export interface PlanPrice {
  /** In the currency's smallest unit — 2900 is $29.00. */
  unit_amount: number;
  currency: string;
  interval: string;
}

/** One plan on offer: what it buys, what it costs, whether you are on it. */
export interface PlanCatalogEntry {
  entitlements: Entitlements;
  /** Null for the free tier, which is not a Stripe product. */
  price: PlanPrice | null;
  description: string | null;
  is_current: boolean;
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

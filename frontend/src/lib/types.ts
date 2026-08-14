export interface User {
  id: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  is_verified: boolean;
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

"use client";

import { useCallback, useEffect, useState } from "react";

import { getAccessToken, fetchCurrentUser, logout as logoutRequest } from "@/lib/auth";
import type { User } from "@/lib/types";

interface UseAuthResult {
  user: User | null;
  loading: boolean;
  logout: () => void;
  refresh: () => Promise<void>;
}

export function useAuth(): UseAuthResult {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState<boolean>(() => Boolean(getAccessToken()));

  const refresh = useCallback(async () => {
    if (!getAccessToken()) {
      return;
    }
    try {
      const current = await fetchCurrentUser();
      setUser(current);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // refresh() only sets state after awaiting fetchCurrentUser(); the linter
    // can't trace that gap through the useCallback indirection.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh();
  }, [refresh]);

  const logout = useCallback(() => {
    logoutRequest();
    setUser(null);
  }, []);

  return { user, loading, logout, refresh };
}

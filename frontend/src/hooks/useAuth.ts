import { useCallback, useEffect, useState } from "react";

import { flushEvents, resetAnalytics } from "@/lib/analytics";
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
    // Flush before the token goes: ingest is authenticated, so anything
    // still buffered afterwards could never be sent. resetAnalytics then
    // clears whatever the flush didn't take, so this session's events can't
    // be attributed to whoever logs in next on this browser.
    void flushEvents();
    resetAnalytics();
    logoutRequest();
    setUser(null);
  }, []);

  return { user, loading, logout, refresh };
}

import { useCallback, useEffect, useState } from "react";

import { ApiClientError } from "@/lib/api-client";
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
    } catch (error) {
      // A rejected token is dead: drop it, along with any events buffered
      // against it. Leaving it in storage means every protected route makes
      // the same doomed request, and trackEvent() keeps queueing events it
      // can never deliver because a token is present.
      //
      // Only on an explicit rejection, though. A network failure is not a
      // logout — clearing tokens on a dropped connection would sign people
      // out for going through a tunnel.
      if (error instanceof ApiClientError && error.status === 401) {
        resetAnalytics();
        logoutRequest();
      }
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

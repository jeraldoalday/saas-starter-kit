import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { trackEvent } from "@/lib/analytics";
import { ApiClientError } from "@/lib/api-client";
import { formatRelativeTime } from "@/lib/api-keys";
import { clearTokens } from "@/lib/auth";
import {
  describeUserAgent,
  fetchSessions,
  isSessionActive,
  revokeAllSessions,
  revokeSession,
  sessionStatusLabel,
  sortSessions,
} from "@/lib/sessions";
import type { AuthSession } from "@/lib/types";

function statusClasses(session: AuthSession): string {
  return isSessionActive(session) ? "text-emerald-400" : "text-neutral-500";
}

export function SessionsPage() {
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<AuthSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Which session the Sign out button is asking about. A second click
  // confirms; anything else cancels. Deliberately not window.confirm, which
  // blocks the event loop and can't be styled or tested — the same choice
  // the API keys page makes for revocation.
  const [confirming, setConfirming] = useState<string | null>(null);
  const [confirmingAll, setConfirmingAll] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setSessions(await fetchSessions());
      setError(null);
    } catch {
      setError("Could not load your sessions.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  useEffect(() => {
    trackEvent("sessions.viewed");
  }, []);

  async function handleRevoke(session: AuthSession) {
    if (confirming !== session.id) {
      setConfirming(session.id);
      return;
    }

    setConfirming(null);
    setError(null);
    try {
      await revokeSession(session.id);
      trackEvent("sessions.revoked");
      await load();
    } catch (err) {
      setError(
        err instanceof ApiClientError ? err.message : "Could not sign that device out."
      );
    }
  }

  async function handleRevokeOthers() {
    if (!confirmingAll) {
      setConfirmingAll(true);
      return;
    }

    setConfirmingAll(false);
    setError(null);
    try {
      const { revoked } = await revokeAllSessions(true);
      trackEvent("sessions.revoked_others", { revoked });
      setNotice(
        revoked === 0
          ? "No other devices were signed in."
          : `Signed out ${revoked} other device${revoked === 1 ? "" : "s"}.`
      );
      await load();
    } catch (err) {
      setError(
        err instanceof ApiClientError
          ? err.message
          : "Could not sign your other devices out."
      );
    }
  }

  async function handleRevokeEverywhere() {
    setError(null);
    try {
      await revokeAllSessions(false);
      trackEvent("sessions.revoked_all");
      // This browser's own session is among the ones just revoked, so its
      // stored tokens are already dead. Clearing and leaving is the honest
      // response — staying on the page would show a dashboard backed by
      // credentials that no longer work until the next request failed.
      clearTokens();
      navigate("/login");
    } catch (err) {
      setError(
        err instanceof ApiClientError ? err.message : "Could not sign out everywhere."
      );
    }
  }

  const sorted = sortSessions(sessions);
  const activeCount = sorted.filter((session) => isSessionActive(session)).length;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold text-neutral-100">Sessions</h1>
        <p className="mt-1 text-sm text-neutral-400">
          Every device signed in to this account. Signing one out takes effect
          immediately — it cannot renew, and it stops working within the hour.
        </p>
      </div>

      {notice && (
        <p className="text-sm text-emerald-400" role="status">
          {notice}
        </p>
      )}

      <Card>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-neutral-100">
            Active devices{activeCount > 0 ? ` (${activeCount})` : ""}
          </h2>
          {activeCount > 1 && (
            <button
              type="button"
              onClick={handleRevokeOthers}
              onBlur={() => setConfirmingAll(false)}
              className="text-xs text-red-400 transition-colors hover:text-red-300"
            >
              {confirmingAll ? "Click to confirm" : "Sign out other devices"}
            </button>
          )}
        </div>

        {loading && <p className="text-sm text-neutral-500">Loading…</p>}

        {!loading && sorted.length === 0 && (
          <p className="text-sm text-neutral-500">No sessions to show.</p>
        )}

        {!loading && sorted.length > 0 && (
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-neutral-500">
              <tr>
                <th scope="col" className="pb-2 font-medium">Device</th>
                <th scope="col" className="pb-2 font-medium">IP address</th>
                <th scope="col" className="pb-2 font-medium">Status</th>
                <th scope="col" className="pb-2 font-medium">Last active</th>
                <th scope="col" className="pb-2 font-medium sr-only">Actions</th>
              </tr>
            </thead>
            <tbody className="text-neutral-400">
              {sorted.map((session) => (
                <tr key={session.id} className="border-t border-neutral-800">
                  <td className="py-3 pr-4 text-neutral-200">
                    {describeUserAgent(session.user_agent)}
                  </td>
                  <td className="py-3 pr-4 font-mono text-xs">
                    {session.ip_address ?? "—"}
                  </td>
                  <td className={`py-3 pr-4 ${statusClasses(session)}`}>
                    {sessionStatusLabel(session)}
                  </td>
                  <td className="py-3 pr-4">
                    {formatRelativeTime(session.last_used_at)}
                  </td>
                  <td className="py-3 text-right">
                    {isSessionActive(session) && (
                      <button
                        type="button"
                        onClick={() => handleRevoke(session)}
                        onBlur={() => setConfirming(null)}
                        className="text-xs text-red-400 transition-colors hover:text-red-300"
                      >
                        {confirming === session.id ? "Click to confirm" : "Sign out"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {error && (
          <p className="mt-4 text-sm text-red-400" role="alert">
            {error}
          </p>
        )}
      </Card>

      <Card>
        <h2 className="mb-2 text-sm font-semibold text-neutral-100">
          Lost a device?
        </h2>
        <p className="mb-4 text-sm text-neutral-400">
          Sign out everywhere, including this browser, and sign back in with
          your password. Changing your password does this automatically.
        </p>
        <Button variant="secondary" onClick={handleRevokeEverywhere}>
          Sign out everywhere
        </Button>
      </Card>
    </div>
  );
}

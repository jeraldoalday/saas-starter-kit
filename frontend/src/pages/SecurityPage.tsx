import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { trackEvent } from "@/lib/analytics";
import { formatRelativeTime } from "@/lib/api-keys";
import {
  auditTone,
  describeAuditAction,
  describeAuditContext,
  fetchAuditPage,
  hasMorePages,
  PAGE_SIZE,
  securityHighlights,
} from "@/lib/audit";
import { describeUserAgent } from "@/lib/sessions";
import type { AuditEntry } from "@/lib/types";

const TONE_CLASSES = {
  alert: "text-red-400",
  notable: "text-amber-400",
  normal: "text-neutral-200",
} as const;

export function SecurityPage() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [more, setMore] = useState(false);
  // How far back the plan lets this account read, or null for no limit.
  // Shown because a trail that simply stops reads as rows somebody deleted.
  const [historyDays, setHistoryDays] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadFirstPage = useCallback(async () => {
    setLoading(true);
    try {
      const page = await fetchAuditPage(0);
      setEntries(page.entries);
      setTotal(page.total);
      setMore(hasMorePages(page));
      setHistoryDays(page.history_days);
      setError(null);
    } catch {
      setError("Could not load your security history.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadFirstPage();
  }, [loadFirstPage]);

  useEffect(() => {
    trackEvent("security.viewed");
  }, []);

  async function loadMore() {
    setLoadingMore(true);
    setError(null);
    try {
      // Offset from what is already on screen rather than from a page
      // counter: the trail grows while somebody reads it, and a counter
      // would quietly skip whatever arrived in between.
      const page = await fetchAuditPage(entries.length, PAGE_SIZE);
      setEntries((current) => [...current, ...page.entries]);
      setTotal(page.total);
      setMore(hasMorePages(page));
    } catch {
      setError("Could not load more history.");
    } finally {
      setLoadingMore(false);
    }
  }

  const highlights = securityHighlights(entries);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold text-neutral-100">Security</h1>
        <p className="mt-1 text-sm text-neutral-400">
          Everything that has happened to this account: every sign-in and every
          refused attempt, the devices and keys that came and went, and any
          change an administrator made. If you do not recognise something here,
          change your password and sign out everywhere.
        </p>
      </div>

      {highlights.length > 0 && (
        <Card className="border-amber-900/60 bg-amber-950/20">
          <h2 className="mb-2 text-sm font-semibold text-neutral-100">
            Worth a look
          </h2>
          <ul className="flex flex-col gap-1 text-sm text-neutral-300">
            {highlights.map((entry) => (
              <li key={entry.id}>
                <span className={TONE_CLASSES[auditTone(entry)]}>
                  {describeAuditAction(entry.action)}
                </span>{" "}
                <span className="text-neutral-500">
                  {formatRelativeTime(entry.created_at)}
                  {entry.ip_address !== null && ` from ${entry.ip_address}`}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-neutral-100">Activity</h2>
          {total > 0 && (
            <span className="text-xs text-neutral-500">
              Showing {entries.length} of {total}
              {historyDays !== null && ` from the last ${historyDays} days`}
            </span>
          )}
        </div>

        {loading && <p className="text-sm text-neutral-500">Loading…</p>}

        {!loading && entries.length === 0 && !error && (
          <p className="text-sm text-neutral-500">Nothing recorded yet.</p>
        )}

        {!loading && entries.length > 0 && (
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-neutral-500">
              <tr>
                <th scope="col" className="pb-2 font-medium">Event</th>
                <th scope="col" className="pb-2 font-medium">Detail</th>
                <th scope="col" className="pb-2 font-medium">Device</th>
                <th scope="col" className="pb-2 font-medium">IP address</th>
                <th scope="col" className="pb-2 font-medium">When</th>
              </tr>
            </thead>
            <tbody className="text-neutral-400">
              {entries.map((entry) => (
                <tr key={entry.id} className="border-t border-neutral-800">
                  <td className={`py-3 pr-4 ${TONE_CLASSES[auditTone(entry)]}`}>
                    {describeAuditAction(entry.action)}
                    {entry.by_another_account && (
                      <span className="ml-2 text-xs text-neutral-500">
                        by an administrator
                      </span>
                    )}
                  </td>
                  <td className="py-3 pr-4">
                    {describeAuditContext(entry) ?? "—"}
                  </td>
                  <td className="py-3 pr-4">
                    {describeUserAgent(entry.user_agent)}
                  </td>
                  <td className="py-3 pr-4 font-mono text-xs">
                    {entry.ip_address ?? "—"}
                  </td>
                  <td className="py-3 pr-4">
                    {formatRelativeTime(entry.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {more && (
          <div className="mt-4">
            <Button
              variant="secondary"
              onClick={loadMore}
              disabled={loadingMore}
            >
              {loadingMore ? "Loading…" : "Load more"}
            </Button>
          </div>
        )}

        {error && (
          <p className="mt-4 text-sm text-red-400" role="alert">
            {error}
          </p>
        )}
      </Card>
    </div>
  );
}

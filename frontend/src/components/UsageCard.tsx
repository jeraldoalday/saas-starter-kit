import { useCallback, useEffect, useState } from "react";

import { Card } from "@/components/ui/Card";
import { Sparkline } from "@/components/ui/Sparkline";
import { fetchMyUsage, humanizeEventName } from "@/lib/analytics";
import type { UsageSummary } from "@/lib/types";

/** Windows offered by the toggle, in days. */
const WINDOWS = [7, 14, 30] as const;

/** How many of the most frequent events to list. */
const TOP_EVENTS = 5;

export function UsageCard() {
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [days, setDays] = useState<number>(14);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async (window: number) => {
    setLoading(true);
    try {
      setSummary(await fetchMyUsage(window));
      setFailed(false);
    } catch {
      // Usage is a nice-to-have panel; a failure here says so and leaves the
      // rest of the dashboard alone.
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load(days);
  }, [load, days]);

  const topEvents = summary
    ? Object.entries(summary.events_by_name).slice(0, TOP_EVENTS)
    : [];

  return (
    <Card>
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-neutral-100">Your activity</h2>
        <div className="flex gap-1" role="group" aria-label="Activity window">
          {WINDOWS.map((window) => (
            <button
              key={window}
              type="button"
              onClick={() => setDays(window)}
              aria-pressed={days === window}
              className={`rounded px-2 py-1 text-xs transition-colors ${
                days === window
                  ? "bg-neutral-800 text-neutral-100"
                  : "text-neutral-500 hover:text-neutral-300"
              }`}
            >
              {window}d
            </button>
          ))}
        </div>
      </div>

      {loading && <p className="text-sm text-neutral-500">Loading…</p>}

      {!loading && failed && (
        <p className="text-sm text-neutral-500">Usage data is unavailable right now.</p>
      )}

      {!loading && !failed && summary && (
        <>
          <p className="mb-3 text-sm text-neutral-400">
            {summary.total_events === 0
              ? `No recorded activity in the last ${summary.window_days} days.`
              : `${summary.total_events} events in the last ${summary.window_days} days.`}
          </p>

          <Sparkline
            values={summary.daily.map((entry) => entry.count)}
            label={`Daily activity over the last ${summary.window_days} days`}
          />

          {topEvents.length > 0 && (
            <dl className="mt-4 grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm text-neutral-400">
              {topEvents.map(([name, count]) => (
                <div key={name} className="contents">
                  <dt className="truncate">{humanizeEventName(name)}</dt>
                  <dd className="tabular-nums text-neutral-300">{count}</dd>
                </div>
              ))}
            </dl>
          )}
        </>
      )}
    </Card>
  );
}

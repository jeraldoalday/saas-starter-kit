import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { ApiClientError } from "@/lib/api-client";
import {
  createApiKey,
  fetchApiKeys,
  formatRelativeTime,
  isKeyUsable,
  keyStatusLabel,
  revokeApiKey,
  sortKeys,
} from "@/lib/api-keys";
import { trackEvent } from "@/lib/analytics";
import { fetchPlan, formatUsage } from "@/lib/plan";
import type { ApiKey, CreatedApiKey, Quota } from "@/lib/types";

/** Expiry choices, in days. `null` is "does not expire". */
const EXPIRY_OPTIONS: { label: string; value: number | null }[] = [
  { label: "No expiry", value: null },
  { label: "30 days", value: 30 },
  { label: "90 days", value: 90 },
  { label: "1 year", value: 365 },
];

function statusClasses(key: ApiKey): string {
  return isKeyUsable(key) ? "text-emerald-400" : "text-neutral-500";
}

/**
 * The one-time reveal.
 *
 * Rendered as its own block rather than inline in the table because it is
 * the only moment the secret exists: once this unmounts, nobody — including
 * the server — can produce it again.
 */
function CreatedKeyNotice({ created }: { created: CreatedApiKey }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(created.key);
      setCopied(true);
    } catch {
      // Clipboard access is permission-gated and absent over plain HTTP;
      // the secret is on screen either way, so this is not worth an error.
      setCopied(false);
    }
  }

  return (
    <div
      className="mb-6 rounded-md border border-emerald-800 bg-emerald-950/40 p-4"
      role="status"
    >
      <p className="mb-2 text-sm font-medium text-emerald-300">
        Copy your key now — it will not be shown again.
      </p>
      <div className="flex items-center gap-2">
        <code className="flex-1 truncate rounded bg-neutral-950 px-3 py-2 font-mono text-xs text-neutral-200">
          {created.key}
        </code>
        <Button variant="secondary" onClick={copy}>
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
    </div>
  );
}

export function ApiKeysPage() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState("");
  const [expiresInDays, setExpiresInDays] = useState<number | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<CreatedApiKey | null>(null);
  // Which key the Revoke button is currently asking about. A second click
  // confirms; anything else cancels. Deliberately not window.confirm, which
  // blocks the event loop and can't be styled or tested.
  const [confirming, setConfirming] = useState<string | null>(null);
  // The plan's key allowance, or null when the plan could not be loaded.
  // Null means "say nothing about limits" rather than "no limit": guessing
  // either way would be worse than leaving the line off.
  const [quota, setQuota] = useState<Quota | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setKeys(await fetchApiKeys());
      setError(null);
    } catch {
      setError("Could not load your API keys.");
    } finally {
      setLoading(false);
    }

    // Fetched separately and swallowed on failure: the allowance is a line
    // of context, and losing it must not turn a working page into an error.
    try {
      const plan = await fetchPlan();
      setQuota(plan.quotas.find((entry) => entry.key === "api_keys") ?? null);
    } catch {
      setQuota(null);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  useEffect(() => {
    trackEvent("api_keys.viewed");
  }, []);

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) {
      setError("Give the key a name so you can recognise it later.");
      return;
    }

    setPending(true);
    setError(null);
    try {
      const key = await createApiKey(name.trim(), expiresInDays);
      setCreated(key);
      setName("");
      trackEvent("api_keys.created", { expires: expiresInDays !== null });
      await load();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Could not create the key.");
    } finally {
      setPending(false);
    }
  }

  async function handleRevoke(key: ApiKey) {
    if (confirming !== key.id) {
      setConfirming(key.id);
      return;
    }

    setConfirming(null);
    setError(null);
    try {
      await revokeApiKey(key.id);
      // Hide a secret that has just been revoked: leaving it on screen
      // invites pasting a key that no longer works into a config file.
      if (created?.id === key.id) setCreated(null);
      trackEvent("api_keys.revoked");
      await load();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Could not revoke the key.");
    }
  }

  const sorted = sortKeys(keys);
  // Disabling the button rather than letting the request 409 — the ceiling
  // is knowable before the click, and a form that submits only to be
  // refused teaches nothing about how to fix it.
  const atLimit = quota?.exhausted ?? false;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold text-neutral-100">API keys</h1>
        <p className="mt-1 text-sm text-neutral-400">
          Call the API from a script or a scheduled job without a browser session.
          Send the key as <code className="font-mono text-neutral-300">X-API-Key</code>{" "}
          or as a bearer token.
        </p>
      </div>

      {created && <CreatedKeyNotice created={created} />}

      <Card>
        <h2 className="mb-4 text-sm font-semibold text-neutral-100">Create a key</h2>
        <form className="flex flex-col gap-4 sm:flex-row sm:items-end" onSubmit={handleCreate}>
          <div className="flex-1">
            <Input
              id="api-key-name"
              label="Name"
              placeholder="CI deploy"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={64}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="api-key-expiry" className="text-sm font-medium text-neutral-300">
              Expires
            </label>
            <select
              id="api-key-expiry"
              className="rounded-md border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-indigo-500"
              value={expiresInDays === null ? "" : String(expiresInDays)}
              onChange={(event) =>
                setExpiresInDays(event.target.value === "" ? null : Number(event.target.value))
              }
            >
              {EXPIRY_OPTIONS.map((option) => (
                <option key={option.label} value={option.value === null ? "" : option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <Button type="submit" disabled={pending || atLimit}>
            {pending ? "Creating…" : "Create key"}
          </Button>
        </form>

        {quota && (
          <p className={`mt-4 text-sm ${atLimit ? "text-amber-400" : "text-neutral-500"}`}>
            {atLimit
              ? `You have used all ${quota.limit} keys your plan allows. Revoke one you no longer use, or upgrade.`
              : `Using ${formatUsage(quota)} keys on your plan.`}
          </p>
        )}

        {error && (
          <p className="mt-4 text-sm text-red-400" role="alert">
            {error}
          </p>
        )}
      </Card>

      <Card>
        <h2 className="mb-4 text-sm font-semibold text-neutral-100">Your keys</h2>

        {loading && <p className="text-sm text-neutral-500">Loading…</p>}

        {!loading && sorted.length === 0 && (
          <p className="text-sm text-neutral-500">
            No API keys yet. Create one above to call the API from a script.
          </p>
        )}

        {!loading && sorted.length > 0 && (
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-neutral-500">
              <tr>
                <th scope="col" className="pb-2 font-medium">Name</th>
                <th scope="col" className="pb-2 font-medium">Key</th>
                <th scope="col" className="pb-2 font-medium">Status</th>
                <th scope="col" className="pb-2 font-medium">Last used</th>
                <th scope="col" className="pb-2 font-medium sr-only">Actions</th>
              </tr>
            </thead>
            <tbody className="text-neutral-400">
              {sorted.map((key) => (
                <tr key={key.id} className="border-t border-neutral-800">
                  <td className="py-3 pr-4 text-neutral-200">{key.name}</td>
                  <td className="py-3 pr-4 font-mono text-xs">{key.prefix}…</td>
                  <td className={`py-3 pr-4 ${statusClasses(key)}`}>
                    {keyStatusLabel(key)}
                  </td>
                  <td className="py-3 pr-4">{formatRelativeTime(key.last_used_at)}</td>
                  <td className="py-3 text-right">
                    {isKeyUsable(key) && (
                      <button
                        type="button"
                        onClick={() => handleRevoke(key)}
                        onBlur={() => setConfirming(null)}
                        className="text-xs text-red-400 transition-colors hover:text-red-300"
                      >
                        {confirming === key.id ? "Click to confirm" : "Revoke"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { trackEvent } from "@/lib/analytics";
import { ApiClientError } from "@/lib/api-client";
import { formatRelativeTime } from "@/lib/api-keys";
import { fetchPlan, formatUsage } from "@/lib/plan";
import type {
  Quota,
  WebhookDelivery,
  WebhookEndpoint,
  WebhookEventType,
} from "@/lib/types";
import {
  createWebhookEndpoint,
  deleteWebhookEndpoint,
  deliveryOutcome,
  deliveryStatusTone,
  endpointStatusDetail,
  endpointStatusLabel,
  eventTypeLabel,
  fetchWebhookDeliveries,
  fetchWebhookEndpoints,
  fetchWebhookEventTypes,
  looksLikeUrl,
  pingWebhookEndpoint,
  revealWebhookSecret,
  rotateWebhookSecret,
  sortEndpoints,
  updateWebhookEndpoint,
} from "@/lib/webhooks";

function statusClasses(endpoint: WebhookEndpoint): string {
  if (endpoint.disabled_at !== null) return "text-red-400";
  return endpoint.is_active ? "text-emerald-400" : "text-neutral-500";
}

/**
 * The signing secret, shown on demand.
 *
 * Deliberately not fetched with the endpoint list: the API keeps secrets
 * out of the listing so that the common case — a page open in a tab,
 * polling — never carries them, and every reveal is recorded in the
 * account's own security trail. Which means this component fetches, and
 * only when someone asks.
 */
function SecretReveal({
  endpointId,
  providedSecret,
}: {
  endpointId: string;
  /**
   * A secret the page already holds — returned at creation, or by the last
   * rotation. It takes precedence over anything this component fetched,
   * which is what makes a rotation appear immediately: seeding state from
   * the prop instead would leave a mounted reveal showing the secret that
   * has just stopped signing anything.
   */
  providedSecret: string | null;
}) {
  const [fetched, setFetched] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const secret = providedSecret ?? fetched;

  async function reveal() {
    setError(null);
    try {
      const revealed = await revealWebhookSecret(endpointId);
      setFetched(revealed.secret);
      trackEvent("webhooks.secret_revealed");
    } catch (err) {
      setError(
        err instanceof ApiClientError ? err.message : "Could not load the secret."
      );
    }
  }

  async function copy() {
    if (secret === null) return;
    try {
      await navigator.clipboard.writeText(secret);
      setCopied(true);
    } catch {
      // Clipboard access is permission-gated and absent over plain HTTP.
      // The secret is on screen either way.
      setCopied(false);
    }
  }

  if (secret === null) {
    return (
      <div>
        <button
          type="button"
          onClick={reveal}
          className="text-xs text-indigo-400 transition-colors hover:text-indigo-300"
        >
          Show signing secret
        </button>
        {error && (
          <p className="mt-1 text-xs text-red-400" role="alert">
            {error}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <code className="flex-1 truncate rounded bg-neutral-950 px-2 py-1 font-mono text-xs text-neutral-200">
        {secret}
      </code>
      <button
        type="button"
        onClick={copy}
        className="text-xs text-indigo-400 transition-colors hover:text-indigo-300"
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

export function WebhooksPage() {
  const [endpoints, setEndpoints] = useState<WebhookEndpoint[]>([]);
  const [deliveries, setDeliveries] = useState<WebhookDelivery[]>([]);
  const [catalog, setCatalog] = useState<WebhookEventType[]>([]);
  const [loading, setLoading] = useState(true);
  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Secrets this page has been handed: the one returned at creation, and
  // any rotated since. Held per endpoint rather than as a single "current"
  // value, so rotating one endpoint does not blank another's reveal.
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  // Two-click confirmation, as on the API keys and sessions pages: a second
  // click confirms, anything else cancels. Not window.confirm, which blocks
  // the event loop and cannot be styled or tested.
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [confirmingRotate, setConfirmingRotate] = useState<string | null>(null);
  const [quota, setQuota] = useState<Quota | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [loadedEndpoints, loadedDeliveries] = await Promise.all([
        fetchWebhookEndpoints(),
        fetchWebhookDeliveries(),
      ]);
      setEndpoints(loadedEndpoints);
      setDeliveries(loadedDeliveries);
      setError(null);
    } catch {
      setError("Could not load your webhook endpoints.");
    } finally {
      setLoading(false);
    }

    // The catalog and the allowance are context rather than content: each
    // is fetched separately and swallowed on failure, so losing either does
    // not turn a working page into an error.
    try {
      setCatalog(await fetchWebhookEventTypes());
    } catch {
      setCatalog([]);
    }
    try {
      const plan = await fetchPlan();
      setQuota(plan.quotas.find((entry) => entry.key === "webhook_endpoints") ?? null);
    } catch {
      setQuota(null);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  useEffect(() => {
    trackEvent("webhooks.viewed");
  }, []);

  function toggleEventType(eventType: string) {
    setSelected((current) =>
      current.includes(eventType)
        ? current.filter((entry) => entry !== eventType)
        : [...current, eventType]
    );
  }

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    if (!looksLikeUrl(url)) {
      setError("Enter the full URL of your receiver, including https://.");
      return;
    }

    setPending(true);
    setError(null);
    setNotice(null);
    try {
      const created = await createWebhookEndpoint(url.trim(), description.trim(), selected);
      setSecrets((current) => ({ ...current, [created.id]: created.secret }));
      setUrl("");
      setDescription("");
      setSelected([]);
      trackEvent("webhooks.endpoint_created", { events: selected.length });
      setNotice(
        "Endpoint registered. Copy its signing secret into your receiver, then send a test event."
      );
      await load();
    } catch (err) {
      setError(
        err instanceof ApiClientError ? err.message : "Could not register the endpoint."
      );
    } finally {
      setPending(false);
    }
  }

  async function handleToggleActive(endpoint: WebhookEndpoint) {
    setError(null);
    try {
      await updateWebhookEndpoint(endpoint.id, { is_active: !endpoint.is_deliverable });
      trackEvent("webhooks.endpoint_toggled", { active: !endpoint.is_deliverable });
      await load();
    } catch (err) {
      setError(
        err instanceof ApiClientError ? err.message : "Could not update the endpoint."
      );
    }
  }

  async function handlePing(endpoint: WebhookEndpoint) {
    setError(null);
    try {
      await pingWebhookEndpoint(endpoint.id);
      trackEvent("webhooks.pinged");
      // "Queued", not "sent": the delivery worker sends it, so claiming
      // otherwise would be describing something this page cannot know.
      setNotice("Test event queued. It appears in the delivery log once sent.");
      await load();
    } catch (err) {
      setError(
        err instanceof ApiClientError ? err.message : "Could not queue a test event."
      );
    }
  }

  async function handleRotate(endpoint: WebhookEndpoint) {
    if (confirmingRotate !== endpoint.id) {
      setConfirmingRotate(endpoint.id);
      return;
    }

    setConfirmingRotate(null);
    setError(null);
    try {
      const rotated = await rotateWebhookSecret(endpoint.id);
      setSecrets((current) => ({ ...current, [endpoint.id]: rotated.secret }));
      trackEvent("webhooks.secret_rotated");
      setNotice(
        "Secret rotated. Deliveries are signed with the new one from now on — update your receiver."
      );
    } catch (err) {
      setError(
        err instanceof ApiClientError ? err.message : "Could not rotate the secret."
      );
    }
  }

  async function handleDelete(endpoint: WebhookEndpoint) {
    if (confirmingDelete !== endpoint.id) {
      setConfirmingDelete(endpoint.id);
      return;
    }

    setConfirmingDelete(null);
    setError(null);
    try {
      await deleteWebhookEndpoint(endpoint.id);
      setSecrets((current) => {
        const next = { ...current };
        delete next[endpoint.id];
        return next;
      });
      trackEvent("webhooks.endpoint_deleted");
      await load();
    } catch (err) {
      setError(
        err instanceof ApiClientError ? err.message : "Could not delete the endpoint."
      );
    }
  }

  const sorted = sortEndpoints(endpoints);
  // Disabled rather than left to 409, as on the API keys page: the ceiling
  // is knowable before the click, and a form that submits only to be
  // refused teaches nothing about how to fix it.
  const atLimit = quota?.exhausted ?? false;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold text-neutral-100">Webhooks</h1>
        <p className="mt-1 text-sm text-neutral-400">
          Have us POST to your own service when something happens here. Every
          request is signed with the endpoint's secret in{" "}
          <code className="font-mono text-neutral-300">X-Webhook-Signature</code>;
          verify it before you trust the body.
        </p>
      </div>

      {notice && (
        <p className="text-sm text-emerald-400" role="status">
          {notice}
        </p>
      )}

      <Card>
        <h2 className="mb-4 text-sm font-semibold text-neutral-100">Add an endpoint</h2>
        <form className="flex flex-col gap-4" onSubmit={handleCreate}>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-end">
            <div className="flex-1">
              <Input
                id="webhook-url"
                label="Endpoint URL"
                placeholder="https://example.com/hooks/saas"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                maxLength={2048}
              />
            </div>
            <div className="flex-1">
              <Input
                id="webhook-description"
                label="Description"
                placeholder="Production receiver"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                maxLength={120}
              />
            </div>
          </div>

          {catalog.length > 0 && (
            <fieldset className="flex flex-col gap-2">
              <legend className="text-sm font-medium text-neutral-300">
                Events to send
              </legend>
              {catalog.map((entry) => (
                <label
                  key={entry.event_type}
                  className="flex items-start gap-2 text-sm text-neutral-400"
                >
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={selected.includes(entry.event_type)}
                    onChange={() => toggleEventType(entry.event_type)}
                  />
                  <span>
                    <span className="text-neutral-200">{entry.label}</span>{" "}
                    <code className="font-mono text-xs text-neutral-500">
                      {entry.event_type}
                    </code>
                    <span className="block text-xs text-neutral-500">
                      {entry.description}
                    </span>
                  </span>
                </label>
              ))}
            </fieldset>
          )}

          <div>
            <Button type="submit" disabled={pending || atLimit}>
              {pending ? "Adding…" : "Add endpoint"}
            </Button>
          </div>
        </form>

        {quota && (
          <p className={`mt-4 text-sm ${atLimit ? "text-amber-400" : "text-neutral-500"}`}>
            {atLimit
              ? `You have used all ${quota.limit} webhook endpoints your plan allows. Delete one you no longer use, or upgrade.`
              : `Using ${formatUsage(quota)} webhook endpoints on your plan.`}
          </p>
        )}

        {error && (
          <p className="mt-4 text-sm text-red-400" role="alert">
            {error}
          </p>
        )}
      </Card>

      <Card>
        <h2 className="mb-4 text-sm font-semibold text-neutral-100">Your endpoints</h2>

        {loading && <p className="text-sm text-neutral-500">Loading…</p>}

        {!loading && sorted.length === 0 && (
          <p className="text-sm text-neutral-500">
            No endpoints yet. Add one above to have events delivered to your service.
          </p>
        )}

        <ul className="flex flex-col gap-4">
          {sorted.map((endpoint) => {
            const detail = endpointStatusDetail(endpoint);
            return (
              <li
                key={endpoint.id}
                className="border-t border-neutral-800 pt-4 first:border-t-0 first:pt-0"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate font-mono text-sm text-neutral-200">
                      {endpoint.url}
                    </p>
                    {endpoint.description && (
                      <p className="text-xs text-neutral-500">{endpoint.description}</p>
                    )}
                  </div>
                  <span className={`text-xs ${statusClasses(endpoint)}`}>
                    {endpointStatusLabel(endpoint)}
                  </span>
                </div>

                {detail && (
                  <p className="mt-2 text-xs text-amber-400" role="status">
                    {detail}
                  </p>
                )}

                <p className="mt-2 text-xs text-neutral-500">
                  {endpoint.event_types.length === 0
                    ? "No events subscribed — only test events are sent."
                    : endpoint.event_types
                        .map((eventType) => eventTypeLabel(eventType, catalog))
                        .join(", ")}
                </p>

                <div className="mt-3">
                  <SecretReveal
                    endpointId={endpoint.id}
                    providedSecret={secrets[endpoint.id] ?? null}
                  />
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-4 text-xs">
                  <button
                    type="button"
                    onClick={() => handlePing(endpoint)}
                    className="text-indigo-400 transition-colors hover:text-indigo-300"
                  >
                    Send test event
                  </button>
                  <button
                    type="button"
                    onClick={() => handleToggleActive(endpoint)}
                    className="text-neutral-400 transition-colors hover:text-neutral-200"
                  >
                    {endpoint.is_deliverable ? "Switch off" : "Switch on"}
                  </button>
                  <button
                    type="button"
                    onClick={() => handleRotate(endpoint)}
                    onBlur={() => setConfirmingRotate(null)}
                    className="text-amber-400 transition-colors hover:text-amber-300"
                  >
                    {confirmingRotate === endpoint.id
                      ? "Click to confirm — your receiver stops verifying until updated"
                      : "Rotate secret"}
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDelete(endpoint)}
                    onBlur={() => setConfirmingDelete(null)}
                    className="text-red-400 transition-colors hover:text-red-300"
                  >
                    {confirmingDelete === endpoint.id ? "Click to confirm" : "Delete"}
                  </button>
                  <span className="text-neutral-600">
                    Last delivery {formatRelativeTime(endpoint.last_success_at)}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      </Card>

      <Card>
        <h2 className="mb-1 text-sm font-semibold text-neutral-100">Recent deliveries</h2>
        <p className="mb-4 text-xs text-neutral-500">
          Queued events are sent by a background worker, so a delivery appears
          here before it has been attempted. Failures are retried with a
          widening backoff for about nine hours before they are given up on.
        </p>

        {!loading && deliveries.length === 0 && (
          <p className="text-sm text-neutral-500">
            Nothing delivered yet. Send a test event to check your receiver.
          </p>
        )}

        {deliveries.length > 0 && (
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-neutral-500">
              <tr>
                <th scope="col" className="pb-2 font-medium">Event</th>
                <th scope="col" className="pb-2 font-medium">Status</th>
                <th scope="col" className="pb-2 font-medium">Outcome</th>
                <th scope="col" className="pb-2 font-medium">Attempts</th>
                <th scope="col" className="pb-2 font-medium">When</th>
              </tr>
            </thead>
            <tbody className="text-neutral-400">
              {deliveries.map((delivery) => (
                <tr key={delivery.id} className="border-t border-neutral-800">
                  <td className="py-3 pr-4 text-neutral-200">
                    {eventTypeLabel(delivery.event_type, catalog)}
                  </td>
                  <td className={`py-3 pr-4 ${deliveryStatusTone(delivery.status)}`}>
                    {delivery.status}
                  </td>
                  <td className="py-3 pr-4 truncate">{deliveryOutcome(delivery)}</td>
                  <td className="py-3 pr-4">{delivery.attempts}</td>
                  <td className="py-3 pr-4">
                    {formatRelativeTime(delivery.created_at)}
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

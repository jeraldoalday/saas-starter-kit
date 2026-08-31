import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { WebhookDelivery, WebhookEndpoint, WebhookEventType } from "@/lib/types";
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
  looksLikeUrl,
  pingWebhookEndpoint,
  revealWebhookSecret,
  rotateWebhookSecret,
  sortEndpoints,
  updateWebhookEndpoint,
} from "@/lib/webhooks";

function endpoint(overrides: Partial<WebhookEndpoint> = {}): WebhookEndpoint {
  return {
    id: "wh_1",
    url: "https://receiver.example.com/hooks",
    description: "Production receiver",
    event_types: ["api_key.created"],
    is_active: true,
    created_at: "2026-08-01T12:00:00Z",
    disabled_at: null,
    disabled_reason: null,
    consecutive_failures: 0,
    last_success_at: null,
    last_failure_at: null,
    is_deliverable: true,
    ...overrides,
  };
}

function delivery(overrides: Partial<WebhookDelivery> = {}): WebhookDelivery {
  return {
    id: "dl_1",
    endpoint_id: "wh_1",
    event_type: "api_key.created",
    status: "pending",
    attempts: 0,
    next_attempt_at: "2026-08-01T12:00:00Z",
    last_status_code: null,
    last_error: null,
    created_at: "2026-08-01T12:00:00Z",
    delivered_at: null,
    ...overrides,
  };
}

const CATALOG: WebhookEventType[] = [
  {
    event_type: "api_key.created",
    label: "API key created",
    description: "A new API key was minted.",
  },
];

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "OK",
    json: async () => body,
  } as Response;
}

describe("endpointStatusLabel", () => {
  it("calls a working endpoint active", () => {
    expect(endpointStatusLabel(endpoint())).toBe("Active");
  });

  it("distinguishes switched off from failing", () => {
    expect(endpointStatusLabel(endpoint({ is_active: false, is_deliverable: false }))).toBe(
      "Off"
    );
    expect(
      endpointStatusLabel(
        endpoint({ disabled_at: "2026-08-02T00:00:00Z", is_deliverable: false })
      )
    ).toBe("Failing");
  });

  it("reports failing even while the owner's own switch is on", () => {
    // The two switches are separate columns on purpose: the owner never
    // turned this off, delivery gave up on it, and the words have to say so.
    const auto = endpoint({
      is_active: true,
      disabled_at: "2026-08-02T00:00:00Z",
      is_deliverable: false,
    });

    expect(endpointStatusLabel(auto)).toBe("Failing");
  });
});

describe("endpointStatusDetail", () => {
  it("says nothing about a working endpoint", () => {
    expect(endpointStatusDetail(endpoint())).toBeNull();
  });

  it("passes on the server's reason for an auto-disable", () => {
    const detail = endpointStatusDetail(
      endpoint({
        disabled_at: "2026-08-02T00:00:00Z",
        disabled_reason: "20 consecutive failed deliveries. Last error: HTTP 500",
        is_deliverable: false,
      })
    );

    expect(detail).toContain("HTTP 500");
  });

  it("falls back to a sentence when the server sent no reason", () => {
    const detail = endpointStatusDetail(
      endpoint({ disabled_at: "2026-08-02T00:00:00Z", is_deliverable: false })
    );

    expect(detail).toContain("switched off");
  });

  it("explains a manual switch-off differently", () => {
    const detail = endpointStatusDetail(
      endpoint({ is_active: false, is_deliverable: false })
    );

    expect(detail).toContain("Switched off");
  });
});

describe("deliveryOutcome", () => {
  it("reports a delivered request with its status code", () => {
    expect(deliveryOutcome(delivery({ status: "delivered", last_status_code: 200 }))).toBe(
      "Delivered (200)"
    );
  });

  it("reports a queued delivery as queued rather than as a failure", () => {
    expect(deliveryOutcome(delivery())).toBe("Queued");
  });

  it("keeps the status code of a handler that ran and threw", () => {
    expect(deliveryOutcome(delivery({ status: "failed", last_status_code: 500 }))).toBe(
      "HTTP 500"
    );
  });

  it("reports the transport error when nothing answered", () => {
    // The distinction the API keeps as a null status code: "never reached"
    // is a different problem from "the handler threw", and collapsing them
    // into "failed" throws away the answer.
    const unreachable = delivery({
      status: "failed",
      last_status_code: null,
      last_error: "Connection refused",
    });

    expect(deliveryOutcome(unreachable)).toBe("Connection refused");
  });

  it("falls back when a failure carries neither a code nor an error", () => {
    expect(deliveryOutcome(delivery({ status: "failed" }))).toBe("No response");
  });
});

describe("deliveryStatusTone", () => {
  it.each([
    ["delivered", "emerald"],
    ["failed", "red"],
    ["pending", "amber"],
  ])("tones %s", (status, colour) => {
    expect(deliveryStatusTone(status)).toContain(colour);
  });
});

describe("eventTypeLabel", () => {
  it("uses the catalog's wording", () => {
    expect(eventTypeLabel("api_key.created", CATALOG)).toBe("API key created");
  });

  it("falls back to the raw name for an event this build does not know", () => {
    // A delivery log can hold an event from a build that has since removed
    // it; an empty cell would be worse than the dotted name.
    expect(eventTypeLabel("something.new", CATALOG)).toBe("something.new");
  });

  it("falls back when the catalog could not be loaded at all", () => {
    expect(eventTypeLabel("api_key.created", [])).toBe("api_key.created");
  });
});

describe("sortEndpoints", () => {
  it("puts deliverable endpoints first, then newest first", () => {
    const sorted = sortEndpoints([
      endpoint({ id: "old", created_at: "2026-01-01T00:00:00Z" }),
      endpoint({ id: "off", is_deliverable: false, is_active: false }),
      endpoint({ id: "new", created_at: "2026-08-15T00:00:00Z" }),
    ]);

    expect(sorted.map((entry) => entry.id)).toEqual(["new", "old", "off"]);
  });

  it("does not mutate its input", () => {
    const input = [endpoint({ id: "a" }), endpoint({ id: "b", is_deliverable: false })];

    sortEndpoints(input);

    expect(input.map((entry) => entry.id)).toEqual(["a", "b"]);
  });
});

describe("looksLikeUrl", () => {
  it.each(["https://example.com/hooks", "http://localhost:4000/hooks"])(
    "accepts %s",
    (value) => {
      expect(looksLikeUrl(value)).toBe(true);
    }
  );

  it.each(["", "example.com/hooks", "ftp://example.com", "https://"])(
    "rejects %s",
    (value) => {
      expect(looksLikeUrl(value)).toBe(false);
    }
  );

  it("does not try to reimplement the server's rules", () => {
    // The server decides whether a host resolves somewhere private. A
    // client that guessed would refuse URLs the server accepts the day
    // either list changes.
    expect(looksLikeUrl("https://10.0.0.5/hooks")).toBe(true);
  });
});

describe("requests", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    localStorage.setItem("saas.access_token", "session-token");
  });

  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("lists endpoints with the session token", async () => {
    fetchMock.mockResolvedValue(jsonResponse([endpoint()]));

    await fetchWebhookEndpoints();

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/webhooks/endpoints");
    expect(options.headers.Authorization).toBe("Bearer session-token");
  });

  it("sends the url, description and subscriptions when registering", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ ...endpoint(), secret: "whsec_abc" }, 201)
    );

    const created = await createWebhookEndpoint(
      "https://receiver.example.com/hooks",
      "Production",
      ["api_key.created"]
    );

    const [, options] = fetchMock.mock.calls[0];
    expect(JSON.parse(options.body)).toEqual({
      url: "https://receiver.example.com/hooks",
      description: "Production",
      event_types: ["api_key.created"],
    });
    expect(created.secret).toBe("whsec_abc");
  });

  it("patches only what changed", async () => {
    fetchMock.mockResolvedValue(jsonResponse(endpoint({ is_active: false })));

    await updateWebhookEndpoint("wh_1", { is_active: false });

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/webhooks/endpoints/wh_1");
    expect(options.method).toBe("PATCH");
    expect(JSON.parse(options.body)).toEqual({ is_active: false });
  });

  it("reveals a secret without sending a body", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: "wh_1", secret: "whsec_abc" }));

    const revealed = await revealWebhookSecret("wh_1");

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/webhooks/endpoints/wh_1/secret");
    expect(options.method).toBe("GET");
    expect(revealed.secret).toBe("whsec_abc");
  });

  it("rotates with a POST", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: "wh_1", secret: "whsec_new" }));

    const rotated = await rotateWebhookSecret("wh_1");

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/rotate-secret");
    expect(options.method).toBe("POST");
    expect(rotated.secret).toBe("whsec_new");
  });

  it("queues a test event", async () => {
    fetchMock.mockResolvedValue(jsonResponse(delivery({ event_type: "ping" }), 202));

    const queued = await pingWebhookEndpoint("wh_1");

    expect(fetchMock.mock.calls[0][0]).toContain("/webhooks/endpoints/wh_1/ping");
    expect(queued.event_type).toBe("ping");
  });

  it("filters the delivery log by endpoint when asked", async () => {
    fetchMock.mockResolvedValue(jsonResponse([delivery()]));

    await fetchWebhookDeliveries("wh_1", 10);

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("endpoint_id=wh_1");
    expect(url).toContain("limit=10");
  });

  it("asks for every endpoint's deliveries by default", async () => {
    fetchMock.mockResolvedValue(jsonResponse([]));

    await fetchWebhookDeliveries();

    expect(fetchMock.mock.calls[0][0]).not.toContain("endpoint_id");
  });

  it("deletes by id", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 204 } as Response);

    await deleteWebhookEndpoint("wh_1");

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/webhooks/endpoints/wh_1");
    expect(options.method).toBe("DELETE");
  });

  it("surfaces a plan refusal rather than swallowing it", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { detail: "The Free plan allows at most 1 webhook endpoints (1 in use)" },
        409
      )
    );

    await expect(
      createWebhookEndpoint("https://second.example.com/hooks", "", [])
    ).rejects.toThrow("The Free plan allows at most 1 webhook endpoints");
  });
});

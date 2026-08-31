import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  CreatedWebhookEndpoint,
  WebhookDelivery,
  WebhookEndpoint,
  WebhookEventType,
} from "@/lib/types";
import { WebhooksPage } from "@/pages/WebhooksPage";

vi.mock("@/lib/webhooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/webhooks")>();
  return {
    ...actual,
    fetchWebhookEndpoints: vi.fn(),
    fetchWebhookDeliveries: vi.fn(),
    fetchWebhookEventTypes: vi.fn(),
    createWebhookEndpoint: vi.fn(),
    updateWebhookEndpoint: vi.fn(),
    deleteWebhookEndpoint: vi.fn(),
    revealWebhookSecret: vi.fn(),
    rotateWebhookSecret: vi.fn(),
    pingWebhookEndpoint: vi.fn(),
  };
});

vi.mock("@/lib/analytics", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/analytics")>();
  return { ...actual, trackEvent: vi.fn() };
});

const {
  createWebhookEndpoint,
  deleteWebhookEndpoint,
  fetchWebhookDeliveries,
  fetchWebhookEndpoints,
  fetchWebhookEventTypes,
  pingWebhookEndpoint,
  revealWebhookSecret,
  rotateWebhookSecret,
  updateWebhookEndpoint,
} = await import("@/lib/webhooks");

const fetchEndpointsMock = vi.mocked(fetchWebhookEndpoints);
const fetchDeliveriesMock = vi.mocked(fetchWebhookDeliveries);
const fetchEventTypesMock = vi.mocked(fetchWebhookEventTypes);
const createMock = vi.mocked(createWebhookEndpoint);
const updateMock = vi.mocked(updateWebhookEndpoint);
const deleteMock = vi.mocked(deleteWebhookEndpoint);
const revealMock = vi.mocked(revealWebhookSecret);
const rotateMock = vi.mocked(rotateWebhookSecret);
const pingMock = vi.mocked(pingWebhookEndpoint);

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

function created(overrides: Partial<CreatedWebhookEndpoint> = {}): CreatedWebhookEndpoint {
  return { ...endpoint(), secret: "whsec_the_actual_secret", ...overrides };
}

function delivery(overrides: Partial<WebhookDelivery> = {}): WebhookDelivery {
  return {
    id: "dl_1",
    endpoint_id: "wh_1",
    event_type: "api_key.created",
    status: "delivered",
    attempts: 1,
    next_attempt_at: null,
    last_status_code: 200,
    last_error: null,
    created_at: "2026-08-01T12:00:00Z",
    delivered_at: "2026-08-01T12:00:01Z",
    ...overrides,
  };
}

const CATALOG: WebhookEventType[] = [
  {
    event_type: "api_key.created",
    label: "API key created",
    description: "A new API key was minted for the account.",
  },
  {
    event_type: "billing.subscription_canceled",
    label: "Subscription canceled",
    description: "A subscription ended.",
  },
];

function renderPage() {
  return render(
    <MemoryRouter>
      <WebhooksPage />
    </MemoryRouter>
  );
}

function primed(endpoints: WebhookEndpoint[] = [], deliveries: WebhookDelivery[] = []) {
  fetchEndpointsMock.mockResolvedValue(endpoints);
  fetchDeliveriesMock.mockResolvedValue(deliveries);
  fetchEventTypesMock.mockResolvedValue(CATALOG);
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("WebhooksPage", () => {
  it("lists registered endpoints by URL", async () => {
    primed([endpoint()]);

    renderPage();

    expect(await screen.findByText("https://receiver.example.com/hooks")).toBeTruthy();
    expect(screen.getByText("Active")).toBeTruthy();
  });

  it("says so when there is nothing registered", async () => {
    primed();

    renderPage();

    expect(await screen.findByText(/No endpoints yet/)).toBeTruthy();
  });

  it("never shows a secret it was not handed", async () => {
    // The listing carries no secrets, so the page must offer to fetch one
    // rather than render a blank where a secret would be.
    primed([endpoint()]);

    renderPage();

    expect(await screen.findByText("Show signing secret")).toBeTruthy();
  });

  it("fetches a secret only when asked", async () => {
    primed([endpoint()]);
    revealMock.mockResolvedValue({ id: "wh_1", secret: "whsec_revealed" });

    renderPage();
    fireEvent.click(await screen.findByText("Show signing secret"));

    expect(await screen.findByText("whsec_revealed")).toBeTruthy();
    expect(revealMock).toHaveBeenCalledWith("wh_1");
  });

  it("shows the secret returned at creation without a second request", async () => {
    primed();
    createMock.mockResolvedValue(created());

    renderPage();
    await screen.findByText(/No endpoints yet/);
    fireEvent.change(screen.getByLabelText("Endpoint URL"), {
      target: { value: "https://receiver.example.com/hooks" },
    });
    fetchEndpointsMock.mockResolvedValue([endpoint()]);
    fireEvent.click(screen.getByText("Add endpoint"));

    expect(await screen.findByText("whsec_the_actual_secret")).toBeTruthy();
    expect(revealMock).not.toHaveBeenCalled();
  });

  it("refuses a URL with no scheme before making a request", async () => {
    primed();

    renderPage();
    await screen.findByText(/No endpoints yet/);
    fireEvent.change(screen.getByLabelText("Endpoint URL"), {
      target: { value: "receiver.example.com" },
    });
    fireEvent.click(screen.getByText("Add endpoint"));

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(createMock).not.toHaveBeenCalled();
  });

  it("sends the events that were ticked", async () => {
    primed();
    createMock.mockResolvedValue(created());

    renderPage();
    fireEvent.change(await screen.findByLabelText("Endpoint URL"), {
      target: { value: "https://receiver.example.com/hooks" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /API key created/ }));
    fireEvent.click(screen.getByText("Add endpoint"));

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith(
        "https://receiver.example.com/hooks",
        "",
        ["api_key.created"]
      )
    );
  });

  it("surfaces the API's refusal rather than a generic message", async () => {
    primed();
    const { ApiClientError } = await import("@/lib/api-client");
    createMock.mockRejectedValue(
      new ApiClientError(422, "example.com resolves to a private or reserved address")
    );

    renderPage();
    fireEvent.change(await screen.findByLabelText("Endpoint URL"), {
      target: { value: "https://example.com/hooks" },
    });
    fireEvent.click(screen.getByText("Add endpoint"));

    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "example.com resolves to a private or reserved address"
    );
  });

  it("explains an auto-disabled endpoint with the server's reason", async () => {
    primed([
      endpoint({
        disabled_at: "2026-08-02T00:00:00Z",
        disabled_reason: "20 consecutive failed deliveries. Last error: HTTP 500",
        is_deliverable: false,
      }),
    ]);

    renderPage();

    expect(await screen.findByText("Failing")).toBeTruthy();
    expect(screen.getByText(/Last error: HTTP 500/)).toBeTruthy();
  });

  it("offers to switch a failing endpoint back on", async () => {
    primed([endpoint({ disabled_at: "2026-08-02T00:00:00Z", is_deliverable: false })]);
    updateMock.mockResolvedValue(endpoint());

    renderPage();
    fireEvent.click(await screen.findByText("Switch on"));

    await waitFor(() =>
      expect(updateMock).toHaveBeenCalledWith("wh_1", { is_active: true })
    );
  });

  it("queues a test event and says it was queued, not sent", async () => {
    primed([endpoint()]);
    pingMock.mockResolvedValue(delivery({ event_type: "ping", status: "pending" }));

    renderPage();
    fireEvent.click(await screen.findByText("Send test event"));

    expect(await screen.findByRole("status")).toHaveProperty(
      "textContent",
      "Test event queued. It appears in the delivery log once sent."
    );
  });

  it("warns before rotating, and rotates on the second click", async () => {
    primed([endpoint()]);
    rotateMock.mockResolvedValue({ id: "wh_1", secret: "whsec_rotated" });

    renderPage();
    fireEvent.click(await screen.findByText("Rotate secret"));

    // The first click warns rather than acting: there is no overlap window,
    // so a receiver still checking the old secret rejects everything.
    expect(screen.getByText(/stops verifying until updated/)).toBeTruthy();
    expect(rotateMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText(/stops verifying until updated/));

    await waitFor(() => expect(rotateMock).toHaveBeenCalledWith("wh_1"));
    expect(await screen.findByText("whsec_rotated")).toBeTruthy();
  });

  it("requires a second click to delete", async () => {
    primed([endpoint()]);
    deleteMock.mockResolvedValue(undefined);

    renderPage();
    fireEvent.click(await screen.findByText("Delete"));

    expect(deleteMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("Click to confirm"));

    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("wh_1"));
  });

  it("lists recent deliveries with their outcome", async () => {
    primed([endpoint()], [delivery()]);

    renderPage();

    expect(await screen.findByText("Delivered (200)")).toBeTruthy();
    // The event's label, not its dotted name, where the catalog knows it.
    expect(screen.getAllByText("API key created").length).toBeGreaterThan(0);
  });

  it("keeps working when the event catalog cannot be loaded", async () => {
    // The catalog is context, not content: losing it must not turn a
    // working page into an error.
    fetchEndpointsMock.mockResolvedValue([endpoint()]);
    fetchDeliveriesMock.mockResolvedValue([delivery()]);
    fetchEventTypesMock.mockRejectedValue(new Error("down"));

    renderPage();

    expect(await screen.findByText("https://receiver.example.com/hooks")).toBeTruthy();
    // Falls back to the raw event name rather than an empty cell.
    expect(screen.getAllByText("api_key.created").length).toBeGreaterThan(0);
  });

  it("reports a failure to load the endpoints", async () => {
    fetchEndpointsMock.mockRejectedValue(new Error("down"));
    fetchDeliveriesMock.mockRejectedValue(new Error("down"));
    fetchEventTypesMock.mockResolvedValue(CATALOG);

    renderPage();

    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "Could not load your webhook endpoints."
    );
  });
});

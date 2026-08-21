import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiKeysPage } from "@/pages/ApiKeysPage";
import type { ApiKey, CreatedApiKey } from "@/lib/types";

vi.mock("@/lib/api-keys", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-keys")>();
  return {
    ...actual,
    fetchApiKeys: vi.fn(),
    createApiKey: vi.fn(),
    revokeApiKey: vi.fn(),
  };
});

vi.mock("@/lib/analytics", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/analytics")>();
  return { ...actual, trackEvent: vi.fn() };
});

const { createApiKey, fetchApiKeys, revokeApiKey } = await import("@/lib/api-keys");
const fetchApiKeysMock = vi.mocked(fetchApiKeys);
const createApiKeyMock = vi.mocked(createApiKey);
const revokeApiKeyMock = vi.mocked(revokeApiKey);

function key(overrides: Partial<ApiKey> = {}): ApiKey {
  return {
    id: "key_1",
    name: "CI deploy",
    prefix: "sk_live_ab12",
    created_at: "2026-08-01T12:00:00Z",
    last_used_at: null,
    expires_at: null,
    revoked_at: null,
    is_active: true,
    ...overrides,
  };
}

function created(overrides: Partial<CreatedApiKey> = {}): CreatedApiKey {
  return { ...key(), key: "sk_live_the_actual_secret", ...overrides };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ApiKeysPage />
    </MemoryRouter>
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("ApiKeysPage", () => {
  it("lists existing keys by prefix, never by secret", async () => {
    fetchApiKeysMock.mockResolvedValue([key()]);

    renderPage();

    expect(await screen.findByText("CI deploy")).toBeInTheDocument();
    expect(screen.getByText("sk_live_ab12…")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("Never")).toBeInTheDocument();
  });

  it("says so when there are no keys yet", async () => {
    fetchApiKeysMock.mockResolvedValue([]);

    renderPage();

    expect(
      await screen.findByText(/No API keys yet/)
    ).toBeInTheDocument();
  });

  it("reports a failed load without blanking the page", async () => {
    fetchApiKeysMock.mockRejectedValue(new Error("network"));

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load your API keys."
    );
  });

  it("shows the secret exactly once after creating a key", async () => {
    fetchApiKeysMock.mockResolvedValue([]);
    createApiKeyMock.mockResolvedValue(created());

    renderPage();
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "CI deploy" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create key" }));

    expect(await screen.findByText("sk_live_the_actual_secret")).toBeInTheDocument();
    expect(screen.getByText(/will not be shown again/)).toBeInTheDocument();
  });

  it("sends the chosen expiry", async () => {
    fetchApiKeysMock.mockResolvedValue([]);
    createApiKeyMock.mockResolvedValue(created());

    renderPage();
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "Contractor" },
    });
    fireEvent.change(screen.getByLabelText("Expires"), { target: { value: "90" } });
    fireEvent.click(screen.getByRole("button", { name: "Create key" }));

    await waitFor(() =>
      expect(createApiKeyMock).toHaveBeenCalledWith("Contractor", 90)
    );
  });

  it("defaults to a key that never expires", async () => {
    fetchApiKeysMock.mockResolvedValue([]);
    createApiKeyMock.mockResolvedValue(created());

    renderPage();
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "Forever" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create key" }));

    await waitFor(() => expect(createApiKeyMock).toHaveBeenCalledWith("Forever", null));
  });

  it("refuses a blank name before calling the API", async () => {
    fetchApiKeysMock.mockResolvedValue([]);

    renderPage();
    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Create key" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Give the key a name");
    expect(createApiKeyMock).not.toHaveBeenCalled();
  });

  it("surfaces the API's reason for refusing", async () => {
    fetchApiKeysMock.mockResolvedValue([]);
    const { ApiClientError } = await import("@/lib/api-client");
    createApiKeyMock.mockRejectedValue(
      new ApiClientError(409, "At most 20 active keys per account")
    );

    renderPage();
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "One too many" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create key" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "At most 20 active keys per account"
    );
  });

  it("asks for confirmation before revoking", async () => {
    fetchApiKeysMock.mockResolvedValue([key()]);

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));

    expect(
      screen.getByRole("button", { name: "Click to confirm" })
    ).toBeInTheDocument();
    expect(revokeApiKeyMock).not.toHaveBeenCalled();
  });

  it("revokes on the second click and reloads", async () => {
    fetchApiKeysMock.mockResolvedValue([key()]);
    revokeApiKeyMock.mockResolvedValue(undefined);

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    fireEvent.click(screen.getByRole("button", { name: "Click to confirm" }));

    await waitFor(() => expect(revokeApiKeyMock).toHaveBeenCalledWith("key_1"));
    expect(fetchApiKeysMock).toHaveBeenCalledTimes(2);
  });

  it("offers no revoke button for a key that already stopped working", async () => {
    fetchApiKeysMock.mockResolvedValue([
      key({ revoked_at: "2026-08-02T00:00:00Z", is_active: false }),
    ]);

    renderPage();

    expect(await screen.findByText("Revoked")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Revoke" })).not.toBeInTheDocument();
  });

  it("hides a secret that was just revoked", async () => {
    // Empty on first load, then holding the new key once creation reloads.
    fetchApiKeysMock.mockResolvedValueOnce([]).mockResolvedValue([key()]);
    createApiKeyMock.mockResolvedValue(created());
    revokeApiKeyMock.mockResolvedValue(undefined);

    renderPage();
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "CI deploy" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create key" }));
    await screen.findByText("sk_live_the_actual_secret");

    fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    fireEvent.click(screen.getByRole("button", { name: "Click to confirm" }));

    await waitFor(() =>
      expect(screen.queryByText("sk_live_the_actual_secret")).not.toBeInTheDocument()
    );
  });
});

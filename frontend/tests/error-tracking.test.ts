import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const sentryMock = vi.hoisted(() => ({
  init: vi.fn(),
  captureException: vi.fn(),
}));

vi.mock("@sentry/react", () => sentryMock);

import { initErrorTracking, reportError } from "@/lib/error-tracking";

beforeEach(() => {
  sentryMock.init.mockClear();
  sentryMock.captureException.mockClear();
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("initErrorTracking", () => {
  it("does not initialize Sentry when no DSN is configured", () => {
    vi.stubEnv("VITE_SENTRY_DSN", "");

    initErrorTracking();

    expect(sentryMock.init).not.toHaveBeenCalled();
  });

  it("initializes Sentry with the configured DSN", () => {
    vi.stubEnv("VITE_SENTRY_DSN", "https://public@sentry.example.com/1");

    initErrorTracking();

    expect(sentryMock.init).toHaveBeenCalledWith(
      expect.objectContaining({ dsn: "https://public@sentry.example.com/1" })
    );
  });

  it("prefers an explicit environment over the build mode", () => {
    vi.stubEnv("VITE_SENTRY_DSN", "https://public@sentry.example.com/1");
    vi.stubEnv("VITE_SENTRY_ENVIRONMENT", "staging");

    initErrorTracking();

    expect(sentryMock.init).toHaveBeenCalledWith(
      expect.objectContaining({ environment: "staging" })
    );
  });
});

describe("reportError", () => {
  it("forwards the error to Sentry", () => {
    const error = new Error("boom");

    reportError(error);

    expect(sentryMock.captureException).toHaveBeenCalledWith(error);
  });
});

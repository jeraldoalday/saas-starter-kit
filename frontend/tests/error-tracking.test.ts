import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const sentryMock = vi.hoisted(() => ({
  init: vi.fn(),
  captureException: vi.fn(),
}));

vi.mock("@sentry/nextjs", () => sentryMock);

import { initErrorTracking, reportError } from "@/lib/error-tracking";

describe("initErrorTracking", () => {
  const originalDsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

  beforeEach(() => {
    sentryMock.init.mockClear();
  });

  afterEach(() => {
    process.env.NEXT_PUBLIC_SENTRY_DSN = originalDsn;
  });

  it("does not initialize Sentry when no DSN is configured", () => {
    delete process.env.NEXT_PUBLIC_SENTRY_DSN;

    initErrorTracking();

    expect(sentryMock.init).not.toHaveBeenCalled();
  });

  it("initializes Sentry with the configured DSN", () => {
    process.env.NEXT_PUBLIC_SENTRY_DSN = "https://public@sentry.example.com/1";

    initErrorTracking();

    expect(sentryMock.init).toHaveBeenCalledWith(
      expect.objectContaining({ dsn: "https://public@sentry.example.com/1" }),
    );
  });
});

describe("reportError", () => {
  it("forwards the error to Sentry", () => {
    sentryMock.captureException.mockClear();
    const error = new Error("boom");

    reportError(error);

    expect(sentryMock.captureException).toHaveBeenCalledWith(error);
  });
});

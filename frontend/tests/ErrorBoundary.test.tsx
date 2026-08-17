import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const errorTrackingMock = vi.hoisted(() => ({
  reportError: vi.fn(),
  initErrorTracking: vi.fn(),
}));

vi.mock("@/lib/error-tracking", () => errorTrackingMock);

import { ErrorBoundary } from "@/components/ErrorBoundary";

function Boom(): never {
  throw new Error("boom");
}

beforeEach(() => {
  errorTrackingMock.reportError.mockClear();
  // React logs caught render errors to the console; silence it so a passing
  // run doesn't look like a failing one.
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ErrorBoundary", () => {
  it("renders its children when nothing throws", () => {
    render(
      <ErrorBoundary>
        <p>All good</p>
      </ErrorBoundary>
    );

    expect(screen.getByText("All good")).toBeInTheDocument();
    expect(errorTrackingMock.reportError).not.toHaveBeenCalled();
  });

  it("renders the fallback and reports the error when a child throws", () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>
    );

    expect(screen.getByRole("heading", { name: "Something went wrong." })).toBeInTheDocument();
    expect(errorTrackingMock.reportError).toHaveBeenCalledWith(expect.any(Error));
  });
});

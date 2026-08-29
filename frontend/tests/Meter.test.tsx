import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Meter } from "@/components/ui/Meter";

function renderMeter(value: number, extra: Partial<Parameters<typeof Meter>[0]> = {}) {
  render(<Meter value={value} label="active API keys" valueText="3 of 20" {...extra} />);
  return screen.getByRole("progressbar");
}

describe("Meter", () => {
  it("reports the fraction as a percentage and the reading as words", () => {
    const meter = renderMeter(0.15);

    // A screen reader should hear the fact, not the shape of the drawing.
    expect(meter).toHaveAttribute("aria-valuenow", "15");
    expect(meter).toHaveAttribute("aria-valuetext", "3 of 20");
    expect(meter).toHaveAttribute("aria-label", "active API keys");
  });

  it("clamps a value past the end of the track", () => {
    // Reachable after a downgrade leaves an account over its new ceiling.
    expect(renderMeter(1.8)).toHaveAttribute("aria-valuenow", "100");
  });

  it("clamps a negative value", () => {
    expect(renderMeter(-1)).toHaveAttribute("aria-valuenow", "0");
  });

  it("treats a non-finite value as empty rather than rendering NaN", () => {
    expect(renderMeter(Number.NaN)).toHaveAttribute("aria-valuenow", "0");
  });

  it("colours by tone", () => {
    const meter = renderMeter(1, { tone: "exhausted" });
    expect(meter.firstElementChild?.className).toContain("bg-red-400");
  });
});

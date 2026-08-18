import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Sparkline } from "@/components/ui/Sparkline";

function points(): string {
  const polyline = document.querySelector("polyline");
  return polyline?.getAttribute("points") ?? "";
}

describe("Sparkline", () => {
  it("renders nothing for an empty series", () => {
    const { container } = render(<Sparkline values={[]} label="Empty" />);

    expect(container).toBeEmptyDOMElement();
  });

  it("labels the chart for screen readers", () => {
    render(<Sparkline values={[1, 2, 3]} label="Daily activity" />);

    expect(screen.getByRole("img", { name: "Daily activity" })).toBeInTheDocument();
  });

  it("plots one point per value", () => {
    render(<Sparkline values={[0, 5, 3, 8]} label="Series" />);

    expect(points().split(" ")).toHaveLength(4);
  });

  it("draws the peak above the trough", () => {
    render(<Sparkline values={[1, 9]} label="Series" />);

    const [low, high] = points()
      .split(" ")
      .map((pair) => Number(pair.split(",")[1]));
    // SVG y grows downward, so the larger value sits at the smaller y.
    expect(high).toBeLessThan(low);
  });

  it("draws a flat series along the baseline rather than dividing by zero", () => {
    render(<Sparkline values={[0, 0, 0]} label="Quiet" />);

    expect(points()).not.toContain("NaN");
    const ys = points()
      .split(" ")
      .map((pair) => Number(pair.split(",")[1]));
    expect(new Set(ys).size).toBe(1);
  });

  it("renders a single reading as a dot, since one point makes no line", () => {
    const { container } = render(<Sparkline values={[4]} label="One day" />);

    expect(container.querySelector("polyline")).toBeNull();
    expect(container.querySelector("circle")).not.toBeNull();
  });
});

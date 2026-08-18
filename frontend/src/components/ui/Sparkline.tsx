interface SparklineProps {
  values: number[];
  /** Describes the series for screen readers; the SVG itself is decorative. */
  label: string;
  className?: string;
}

const WIDTH = 240;
const HEIGHT = 48;
const STROKE = 2;

/**
 * A minimal trend line for a short series of counts.
 *
 * Hand-drawn SVG rather than a charting dependency: this renders one
 * polyline from at most a few dozen points, and a library would add more
 * bundle weight than the whole dashboard.
 */
export function Sparkline({ values, label, className = "" }: SparklineProps) {
  if (values.length === 0) return null;

  const peak = Math.max(...values);
  // A flat series (all zeros, or one repeated value) has no range to scale
  // against; drawing it along the baseline is honest, where dividing by a
  // zero range would produce NaN coordinates and an empty chart.
  const usableHeight = HEIGHT - STROKE * 2;
  const step = values.length > 1 ? WIDTH / (values.length - 1) : 0;

  const points = values
    .map((value, index) => {
      const x = values.length > 1 ? index * step : WIDTH / 2;
      const y = peak === 0 ? HEIGHT - STROKE : HEIGHT - STROKE - (value / peak) * usableHeight;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");

  return (
    <svg
      role="img"
      aria-label={label}
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      preserveAspectRatio="none"
      className={`h-12 w-full ${className}`}
    >
      {values.length === 1 ? (
        <circle cx={WIDTH / 2} cy={HEIGHT / 2} r={STROKE} className="fill-indigo-400" />
      ) : (
        <polyline
          points={points}
          fill="none"
          strokeWidth={STROKE}
          strokeLinecap="round"
          strokeLinejoin="round"
          className="stroke-indigo-400"
          vectorEffect="non-scaling-stroke"
        />
      )}
    </svg>
  );
}

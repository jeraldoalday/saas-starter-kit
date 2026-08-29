export type MeterTone = "normal" | "warning" | "exhausted";

interface MeterProps {
  /** How full the bar is, 0–1. Values outside that range are clamped. */
  value: number;
  tone?: MeterTone;
  /** Describes what is being measured, for screen readers. */
  label: string;
  /** The reading itself, announced alongside the label. */
  valueText: string;
  className?: string;
}

const toneClasses: Record<MeterTone, string> = {
  normal: "bg-indigo-400",
  warning: "bg-amber-400",
  exhausted: "bg-red-400",
};

/**
 * A horizontal fill bar for one bounded quantity.
 *
 * Hand-drawn rather than a `<progress>` element, which cannot be styled
 * consistently across browsers without fighting three vendor pseudo-
 * elements — and rather than a charting dependency, for the reason
 * `Sparkline` gives: this is two divs.
 *
 * `role="progressbar"` with an explicit `aria-valuetext` because the number
 * a screen reader should hear is "3 of 20 keys", not "15%". A percentage is
 * the shape of the drawing, not the shape of the fact.
 */
export function Meter({
  value,
  tone = "normal",
  label,
  valueText,
  className = "",
}: MeterProps) {
  const fraction = Math.min(Math.max(Number.isFinite(value) ? value : 0, 0), 1);

  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(fraction * 100)}
      aria-valuetext={valueText}
      className={`h-1.5 w-full overflow-hidden rounded-full bg-neutral-800 ${className}`}
    >
      <div
        className={`h-full rounded-full transition-[width] ${toneClasses[tone]}`}
        style={{ width: `${fraction * 100}%` }}
      />
    </div>
  );
}

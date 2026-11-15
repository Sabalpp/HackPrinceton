import type { LucideIcon } from "lucide-react";

export type RingTone = "primary" | "watch" | "danger" | "muted";

const STROKE: Record<RingTone, string> = {
  primary: "var(--color-primary)",
  watch: "var(--color-watch-label)",
  danger: "var(--color-danger-ink)",
  muted: "var(--color-muted)",
};

/**
 * Circular gauge in the style of fitness apps. `value` is 0..1 of a full ring (clamped).
 * Purely visual; the accessible text comes from `label` + `center` + `caption`.
 */
export function Ring({
  value,
  tone = "primary",
  size = 132,
  thickness = 11,
  center,
  unit,
  label,
  caption,
  Icon,
}: {
  value: number;
  tone?: RingTone;
  size?: number;
  thickness?: number;
  center: string;
  unit?: string;
  label: string;
  caption?: string;
  Icon?: LucideIcon;
}) {
  const r = (size - thickness) / 2;
  const c = 2 * Math.PI * r;
  const v = Math.max(0, Math.min(1, value));
  return (
    <figure className="flex min-w-0 flex-col items-center gap-2 text-center">
      {/* Scales down to fit narrow screens; `size` is the maximum. */}
      <div className="relative w-full" style={{ maxWidth: size, aspectRatio: "1 / 1" }}>
        <svg viewBox={`0 0 ${size} ${size}`} className="h-full w-full -rotate-90" aria-hidden="true">
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--color-ring-track)" strokeWidth={thickness} />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke={STROKE[tone]}
            strokeWidth={thickness}
            strokeLinecap="round"
            strokeDasharray={`${c * v} ${c}`}
            className="transition-[stroke-dasharray] duration-700 ease-out"
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          {Icon && <Icon size={16} className="mb-0.5 hidden text-muted sm:block" aria-hidden="true" />}
          <span className="tabular text-lg font-bold leading-none text-ink sm:text-[1.65rem]">{center}</span>
          {unit && <span className="mt-1 text-[0.6rem] font-bold uppercase tracking-[0.12em] text-muted sm:text-[0.65rem]">{unit}</span>}
        </div>
      </div>
      <figcaption className="flex flex-col">
        <span className="text-[0.68rem] font-bold uppercase tracking-[0.12em] text-body sm:text-xs">{label}</span>
        {caption && <span className="text-sm text-muted">{caption}</span>}
      </figcaption>
    </figure>
  );
}

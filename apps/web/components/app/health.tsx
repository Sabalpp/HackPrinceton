import { Footprints, HeartPulse, Moon, TrendingDown, TrendingUp, Minus, type LucideIcon } from "lucide-react";
import type { HealthObservation, MetricTrend } from "@/lib/api";

export type Metric = MetricTrend["metric"];

export const METRIC: Record<Metric, { label: string; Icon: LucideIcon; unit: string; fmt: (v: number) => string }> = {
  steps: { label: "Daily steps", Icon: Footprints, unit: "steps", fmt: (v) => Math.round(v).toLocaleString("en-US") },
  sleep_minutes: {
    label: "Sleep",
    Icon: Moon,
    unit: "",
    fmt: (v) => `${Math.floor(v / 60)}h ${String(Math.round(v % 60)).padStart(2, "0")}m`,
  },
  resting_heart_rate: { label: "Resting heart rate", Icon: HeartPulse, unit: "bpm", fmt: (v) => `${Math.round(v)}` },
};

export const series = (obs: HealthObservation[], m: Metric) =>
  obs.map((o) => ({ t: o.timestamp, v: o[m] ?? null })).filter((p): p is { t: string; v: number } => p.v !== null);

function Sparkline({ values, highlight }: { values: number[]; highlight: boolean }) {
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => [(i / (values.length - 1)) * 100, 30 - ((v - min) / span) * 26]);
  const line = pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const stroke = highlight ? "var(--color-watch-label)" : "var(--color-primary)";
  return (
    <svg viewBox="0 0 100 32" preserveAspectRatio="none" className="h-10 w-full" aria-hidden="true">
      <polygon points={`0,32 ${line} 100,32`} fill={stroke} opacity="0.12" />
      <polyline points={line} fill="none" stroke={stroke} strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
      <circle cx={pts.at(-1)![0]} cy={pts.at(-1)![1]} r="2.6" fill={stroke} />
    </svg>
  );
}

/** One metric: recent average vs her usual, with direction and a 2-week sparkline. */
export function TrendTile({ trend, obs }: { trend: MetricTrend; obs: HealthObservation[] }) {
  const m = METRIC[trend.metric];
  const vals = series(obs, trend.metric).slice(-14).map((p) => p.v);
  const watch = trend.notable;
  const Arrow = trend.direction === "up" ? TrendingUp : trend.direction === "down" ? TrendingDown : Minus;
  const pct = trend.percent_change;
  return (
    <li className={`flex flex-col gap-2 rounded-2xl border p-5 ${watch ? "border-watch-border bg-watch-bg" : "border-border bg-surface"}`}>
      <p className={`flex items-center gap-2 text-sm font-semibold ${watch ? "text-watch-label" : "text-muted"}`}>
        <m.Icon size={17} aria-hidden="true" />
        {m.label}
      </p>
      {trend.recent !== null ? (
        <p className={`tabular text-3xl font-bold ${watch ? "text-watch-heading" : "text-ink"}`}>
          {m.fmt(trend.recent)}
          {m.unit && <span className="ml-1 text-base font-semibold opacity-70">{m.unit}</span>}
        </p>
      ) : (
        <p className="text-lg font-semibold text-muted">Not enough data</p>
      )}
      <p className={`flex flex-wrap items-center gap-1.5 text-sm ${watch ? "text-watch-body" : "text-body"}`}>
        {pct !== null && trend.direction !== "insufficient_data" && (
          <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-semibold ${watch ? "bg-surface/70" : "bg-inset"}`}>
            <Arrow size={14} aria-hidden="true" />
            {trend.direction === "stable" ? "Steady" : `${Math.abs(Math.round(pct))}% ${pct < 0 ? "lower" : "higher"}`}
          </span>
        )}
        {trend.baseline !== null && <span>usual {m.fmt(trend.baseline)}</span>}
      </p>
      <Sparkline values={vals} highlight={watch} />
      <p className="sr-only">5-day average compared with the previous weeks. {trend.observation}</p>
    </li>
  );
}

/** Daily bars for one metric, last 5 days highlighted, dashed line at her usual (baseline). */
export function MetricChart({ obs, metric, baseline }: { obs: HealthObservation[]; metric: Metric; baseline: number | null }) {
  const pts = series(obs, metric);
  if (pts.length === 0) return <p className="text-muted">No data yet.</p>;
  const m = METRIC[metric];
  const W = 720;
  const H = 230;
  const pad = { l: 56, r: 12, t: 16, b: 30 };
  const max = Math.max(...pts.map((p) => p.v), baseline ?? 0) * 1.12;
  const min = metric === "resting_heart_rate" ? Math.min(...pts.map((p) => p.v)) * 0.85 : 0;
  const y = (v: number) => pad.t + (1 - (v - min) / (max - min)) * (H - pad.t - pad.b);
  const step = (W - pad.l - pad.r) / pts.length;
  const ticks = [min, min + (max - min) / 2, max].map((v) => Math.round(v));
  const day = (iso: string) => new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(iso));

  return (
    <figure className="flex flex-col gap-2">
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full min-w-[520px]" role="img" aria-label={`${m.label}, last ${pts.length} days`}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} stroke="var(--color-border)" strokeWidth="1" />
              <text x={pad.l - 8} y={y(t) + 4} textAnchor="end" fontSize="12" fill="var(--color-muted)">
                {metric === "sleep_minutes" ? `${Math.round(t / 60)}h` : t.toLocaleString("en-US")}
              </text>
            </g>
          ))}
          {pts.map((p, i) => {
            const recent = i >= pts.length - 5;
            const top = y(p.v);
            return (
              <rect
                key={p.t}
                x={pad.l + i * step + step * 0.18}
                y={top}
                width={step * 0.64}
                height={Math.max(1, H - pad.b - top)}
                rx="3"
                fill={recent ? "var(--color-sun)" : "var(--color-primary)"}
                opacity={recent ? 1 : 0.55}
              >
                <title>{`${day(p.t)}: ${m.fmt(p.v)} ${m.unit}`}</title>
              </rect>
            );
          })}
          {baseline !== null && (
            <g>
              <line x1={pad.l} x2={W - pad.r} y1={y(baseline)} y2={y(baseline)} stroke="var(--color-ink)" strokeWidth="1.5" strokeDasharray="5 4" />
              <text x={W - pad.r} y={y(baseline) - 6} textAnchor="end" fontSize="12" fontWeight="600" fill="var(--color-ink)">
                usual {m.fmt(baseline)}
              </text>
            </g>
          )}
          {pts.map((p, i) =>
            i % 7 === 0 || i === pts.length - 1 ? (
              <text key={`x${p.t}`} x={pad.l + i * step + step / 2} y={H - 10} textAnchor="middle" fontSize="12" fill="var(--color-muted)">
                {day(p.t)}
              </text>
            ) : null,
          )}
        </svg>
      </div>
      <figcaption className="flex flex-wrap gap-4 text-sm text-muted">
        <span className="inline-flex items-center gap-1.5">
          <span className="size-3 rounded-sm bg-primary opacity-55" aria-hidden="true" /> Earlier days
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="size-3 rounded-sm bg-sun" aria-hidden="true" /> Last 5 days
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="w-4 border-t-2 border-dashed border-ink" aria-hidden="true" /> Usual (earlier average)
        </span>
      </figcaption>
    </figure>
  );
}

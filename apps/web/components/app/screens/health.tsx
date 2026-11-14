"use client";

import { useState } from "react";
import { HeartPulse, Info, Lightbulb } from "lucide-react";
import { api, type HealthSummary } from "@/lib/api";
import { useLiveQuery } from "@/lib/dashboard";
import { SimulatedPill } from "@/components/farcare/ui";
import { Ready } from "../shell";
import { EmptyBlock, ErrorBlock, LoadingBlock, PageHeader } from "../kit";
import { METRIC, MetricChart, TrendTile, type Metric } from "../health";

const RANGES = [14, 35, 60] as const;
const fetchers = Object.fromEntries(
  RANGES.map((d) => [d, (token: string, parentId: string) => api.healthSummary(token, parentId, d)]),
) as Record<(typeof RANGES)[number], (t: string, p: string) => Promise<HealthSummary | null>>;

export function HealthScreen() {
  return <Ready>{(r) => <Health name={r.overview.parent.display_name} />}</Ready>;
}

function Health({ name }: { name: string }) {
  const [days, setDays] = useState<(typeof RANGES)[number]>(35);
  const [metric, setMetric] = useState<Metric>("steps");
  const { data, error, loading } = useLiveQuery(fetchers[days]);

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        eyebrow="Health"
        title={`${name}'s trends`}
        description={`Daily steps, sleep and resting heart rate, compared with ${name}'s own usual. FarCare looks for changes, it doesn't diagnose.`}
        actions={
          <div role="group" aria-label="Time range" className="flex rounded-[10px] border border-border bg-surface p-1">
            {RANGES.map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setDays(d)}
                aria-pressed={days === d}
                className={`min-h-9 rounded-lg px-3 text-sm font-semibold ${days === d ? "bg-primary text-white" : "text-body hover:bg-inset"}`}
              >
                {d} days
              </button>
            ))}
          </div>
        }
      />

      {loading && !data && <LoadingBlock label="Loading health data" />}
      {error && !data && <ErrorBlock title="Couldn't load health data" message={error} />}
      {data === null && (
        <EmptyBlock title={`${name} hasn't shared health data`} icon={HeartPulse}>
          That&apos;s {name}&apos;s choice. Sharing can be turned on by texting FarCare.
        </EmptyBlock>
      )}

      {data && (
        <>
          <div className="flex flex-wrap items-center gap-3 text-sm text-muted">
            {data.data_mode !== "live" && <SimulatedPill />}
            <span>
              Source: {data.data_mode === "live" ? "Fitbit (Google Health API)" : "simulated demo data"} · {data.observations.length}{" "}
              days
            </span>
          </div>

          {data.suggest_checkin && (
            <div className="flex items-start gap-3 rounded-2xl border border-watch-border bg-watch-bg p-5">
              <Lightbulb size={22} className="mt-0.5 shrink-0 text-watch-label" aria-hidden="true" />
              <div>
                <p className="font-bold text-watch-heading">Something changed this week</p>
                <p className="text-watch-body">This could be a good moment to call {name} and ask how they&apos;re doing.</p>
              </div>
            </div>
          )}

          <ul className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {data.trends.map((t) => (
              <TrendTile key={t.metric} trend={t} obs={data.observations} />
            ))}
          </ul>

          <section className="flex flex-col gap-4 rounded-2xl border border-border bg-surface p-5 sm:p-6" aria-labelledby="chart-title">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 id="chart-title" className="text-lg font-bold text-ink">
                Day by day
              </h2>
              <div role="tablist" aria-label="Metric" className="flex flex-wrap gap-1.5">
                {data.trends.map((t) => {
                  const m = METRIC[t.metric];
                  return (
                    <button
                      key={t.metric}
                      type="button"
                      role="tab"
                      aria-selected={metric === t.metric}
                      onClick={() => setMetric(t.metric)}
                      className={`inline-flex min-h-9 items-center gap-1.5 rounded-full px-3 text-sm font-semibold ${
                        metric === t.metric ? "bg-primary-tint text-on-tint" : "bg-inset text-body hover:text-ink"
                      }`}
                    >
                      <m.Icon size={15} aria-hidden="true" />
                      {m.label}
                    </button>
                  );
                })}
              </div>
            </div>
            <MetricChart obs={data.observations} metric={metric} baseline={data.trends.find((t) => t.metric === metric)?.baseline ?? null} />
          </section>

          <section className="flex flex-col gap-3" aria-labelledby="obs-title">
            <h2 id="obs-title" className="text-lg font-bold text-ink">
              What FarCare noticed
            </h2>
            <ul className="flex flex-col gap-2">
              {data.trends.map((t) => (
                <li key={t.metric} className="flex items-start gap-3 rounded-xl bg-surface p-4 text-body">
                  <span className={`mt-1.5 size-2.5 shrink-0 rounded-full ${t.notable ? "bg-sun" : "bg-primary"}`} aria-hidden="true" />
                  {t.observation}
                </li>
              ))}
            </ul>
          </section>

          <p className="flex items-start gap-2 rounded-xl border border-border bg-inset p-4 text-sm text-body">
            <Info size={18} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" />
            {data.disclaimer}
          </p>
        </>
      )}
    </div>
  );
}

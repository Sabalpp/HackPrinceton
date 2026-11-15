"use client";

import { useState } from "react";
import { BarChart3, CircleCheck, HeartPulse, Info, Lightbulb, Sprout } from "lucide-react";
import { api, type HealthSummary } from "@/lib/api";
import { useLiveQuery } from "@/lib/dashboard";
import { SimulatedPill } from "@/components/farcare/ui";
import { Ready } from "../shell";
import { Card, EmptyBlock, ErrorBlock, LoadingBlock, PageHeader } from "../kit";
import { METRIC, MetricChart, TrendTile, type Metric } from "../health";
import { PersonalHealthPanel } from "@/components/portal/personal-health";
import { LiveSensorPanel } from "@/components/portal/live-sensors";
import { HeartRateAlertCard } from "@/components/portal/heart-rate-alert";

const RANGES = [14, 35, 60] as const;
const fetchers = Object.fromEntries(
  RANGES.map((d) => [d, (token: string, parentId: string) => api.healthSummary(token, parentId, d)]),
) as Record<(typeof RANGES)[number], (t: string, p: string) => Promise<HealthSummary | null>>;

export function HealthScreen() {
  return <Ready>{(r) => <Health name={r.overview.parent.display_name} parentId={r.parentId} />}</Ready>;
}

function Health({ name, parentId }: { name: string; parentId:string }) {
  const [days, setDays] = useState<(typeof RANGES)[number]>(35);
  const [metric, setMetric] = useState<Metric>("steps");
  const { data, error, loading } = useLiveQuery(fetchers[days]);

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        eyebrow="Health"
        title={`${name}'s health`}
        description={`Compared with ${name}'s own usual days. FarCare notices changes; it doesn't diagnose.`}
        actions={
          <div role="group" aria-label="Time range" className="flex rounded-[10px] border border-border bg-surface p-1">
            {RANGES.map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setDays(d)}
                aria-pressed={days === d}
                className={`min-h-11 rounded-lg px-3 text-sm font-semibold ${days === d ? "bg-primary text-on-primary" : "text-body hover:bg-inset"}`}
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
          That&apos;s {name}&apos;s choice. They can change this in their parent screen&apos;s sharing settings.
        </EmptyBlock>
      )}

      {data && (
        <>
          <Noticed data={data} name={name} />
          <LiveSensorPanel parentId={parentId} />
          <HeartRateAlertCard parentId={parentId} name={name} />

          <ul className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {data.trends.map((t) => (
              <TrendTile key={t.metric} trend={t} obs={data.observations} />
            ))}
          </ul>

          <Card
            title="Day by day"
            icon={BarChart3}
            action={
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
                      className={`inline-flex min-h-11 items-center gap-1.5 rounded-full px-3 text-sm font-semibold ${
                        metric === t.metric ? "bg-primary-tint text-on-tint" : "bg-inset text-body hover:text-ink"
                      }`}
                    >
                      <m.Icon size={15} aria-hidden="true" />
                      {m.label}
                    </button>
                  );
                })}
              </div>
            }
          >
            <MetricChart obs={data.observations} metric={metric} baseline={data.trends.find((t) => t.metric === metric)?.baseline ?? null} />
          </Card>

          <div className="flex flex-wrap items-center gap-3 text-sm text-muted">
            {data.data_mode === "simulated" && <SimulatedPill />}
            {data.data_mode === "mixed" && <span className="rounded-full bg-watch-bg px-3 py-1 font-semibold text-watch-heading">Mixed sources</span>}
            <span>
              {data.data_mode === "live" ? "From Fitbit via Google Health" : data.data_mode === "mixed" ? "A mix of watch and simulated records" : data.data_mode === "none" ? "No readings yet" : "Showing simulated demo data"} · {data.observations.length}{" "}
              {data.observations.length === 1 ? "day" : "days"} of readings
            </span>
          </div>

          <PersonalHealthPanel parentId={parentId} />

          <p className="flex items-start gap-2 rounded-xl border border-border bg-inset p-4 text-sm text-body">
            <Info size={18} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" />
            {data.disclaimer}
          </p>
        </>
      )}
    </div>
  );
}

/** One plain answer to "is anything different?", instead of a list per metric. */
function Noticed({ data, name }: { data: HealthSummary; name: string }) {
  const notable = data.trends.filter((t) => t.notable);
  const learning = data.trends.every((t) => t.direction === "insufficient_data");
  if (data.suggest_checkin || notable.length) {
    return (
      <section aria-label="What FarCare noticed" className="flex items-start gap-3 rounded-2xl border border-watch-border bg-watch-bg p-5">
        <Lightbulb size={22} className="mt-0.5 shrink-0 text-watch-label" aria-hidden="true" />
        <div>
          <p className="font-bold text-watch-heading">Something changed this week</p>
          <ul className="mt-1 list-disc pl-5 text-watch-body">{notable.map((t) => <li key={t.metric}>{t.observation}</li>)}</ul>
          <p className="mt-2 text-watch-body">This could be a good moment to call {name} and ask how they&apos;re doing.</p>
        </div>
      </section>
    );
  }
  if (learning) {
    return (
      <section aria-label="What FarCare noticed" className="flex items-start gap-3 rounded-2xl border border-border bg-surface p-5">
        <Sprout size={22} className="mt-0.5 shrink-0 text-primary" aria-hidden="true" />
        <div>
          <p className="font-bold text-ink">Still learning {name}&apos;s usual</p>
          <p className="text-body">Comparisons start after about a week of daily readings. Nothing needs your attention.</p>
        </div>
      </section>
    );
  }
  return (
    <section aria-label="What FarCare noticed" className="flex items-start gap-3 rounded-2xl border border-good-border bg-good-bg p-5">
      <CircleCheck size={22} className="mt-0.5 shrink-0 text-good" aria-hidden="true" />
      <div>
        <p className="font-bold text-good-strong">Nothing unusual this week</p>
        <p className="text-body">{name}&apos;s steps, sleep and resting heart rate look like their usual days.</p>
      </div>
    </section>
  );
}

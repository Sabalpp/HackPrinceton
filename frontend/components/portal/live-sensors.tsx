"use client";

import { useEffect, useState } from "react";
import { HeartPulse, Radio, Watch } from "lucide-react";
import { ApiError } from "@/lib/api";
import { TimeAgo } from "@/components/ui/time";
import { experience, type SensorHealthReport, type SensorMetric } from "@/lib/experience-api";

function number(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : value.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

/** One request at a time; refresh while visible, discard responses after unmount. */
function useSensors(parentId: string) {
  const [data, setData] = useState<SensorHealthReport | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true, pending = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function refresh() {
      if (pending || !active) return;
      clearTimeout(timer);
      if (document.hidden) { timer = setTimeout(refresh, 2000); return; }
      pending = true;
      try {
        const next = await experience.sensorHealth(parentId);
        if (active) { setData(next); setFailed(false); }
      } catch (error) {
        if (active) {
          setFailed(true);
          // A lost permission must also remove any previously displayed value.
          if (error instanceof ApiError && [401, 403, 404].includes(error.status)) setData(null);
        }
      } finally {
        pending = false;
        if (active) { setLoaded(true); timer = setTimeout(refresh, 2000); }
      }
    }
    const resume = () => { if (!document.hidden) void refresh(); };
    void refresh();
    window.addEventListener("focus", resume);
    document.addEventListener("visibilitychange", resume);
    return () => { active = false; clearTimeout(timer); window.removeEventListener("focus", resume); document.removeEventListener("visibilitychange", resume); };
  }, [parentId]);
  return { data, loaded, failed };
}

/** Separate SVG paths preserve gaps; the display never interpolates missing samples. */
function PulseHistory({ points }: { points: SensorHealthReport["recent_heart_rate"] }) {
  if (!points || points.length < 2) return null;
  const sorted = [...points].filter(p => Number.isFinite(p.value) && Number.isFinite(Date.parse(p.observed_at))).sort((a, b) => Date.parse(a.observed_at) - Date.parse(b.observed_at));
  if (sorted.length < 2) return null;
  const start = Date.parse(sorted[0].observed_at), end = Date.parse(sorted.at(-1)!.observed_at);
  const values = sorted.map(p => p.value);
  const low = Math.floor(Math.min(...values) - 3), high = Math.ceil(Math.max(...values) + 3);
  const paths: string[] = [];
  let previous = start, path = "";
  for (const point of sorted) {
    const time = Date.parse(point.observed_at);
    if (time - previous > 5000) { paths.push(path); path = ""; }
    const x = 8 + (time - start) / Math.max(1, end - start) * 584;
    const y = 80 - (point.value - low) / (high - low) * 72;
    path += `${path ? " L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`;
    previous = time;
  }
  paths.push(path);
  return <figure className="mt-4">
    <div className="flex flex-col gap-1 text-xs text-muted sm:flex-row sm:justify-between"><span>Last few minutes</span><span>{low}–{high} bpm</span></div>
    <svg viewBox="0 0 600 88" role="img" aria-label={`Heart rate history: ${sorted.length} received samples, ${Math.min(...values)} to ${Math.max(...values)} beats per minute. Gaps mean missing samples.`} className="mt-2 h-24 w-full text-primary" preserveAspectRatio="none">{paths.filter(Boolean).map((d, i) => <path key={i} d={d} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" />)}</svg>
    <figcaption className="flex justify-between text-xs text-muted"><span>{new Date(start).toLocaleTimeString()}</span><span>{new Date(end).toLocaleTimeString()}</span></figcaption>
  </figure>;
}

/** The few daily numbers families understand, first available of each group; empty ones are hidden. */
const DAILY: { label: string; metrics: string[]; unit: string }[] = [
  { label: "Resting heart rate", metrics: ["resting_heart_rate_bpm"], unit: "bpm" },
  { label: "Sleep", metrics: ["sleep_minutes_asleep"], unit: "min" },
  { label: "Blood oxygen", metrics: ["spo2_daily_mean_percent", "spo2_percent"], unit: "%" },
  { label: "Heart rate variability", metrics: ["hrv_daily_average_ms", "hrv_rmssd_ms"], unit: "ms" },
  { label: "Breathing rate", metrics: ["respiratory_rate_bpm"], unit: "breaths/min" },
];

function dailyValue(label: string, metric: SensorMetric) {
  const v = metric.value!;
  if (label === "Sleep") return `${Math.floor(v / 60)}h ${String(Math.round(v % 60)).padStart(2, "0")}m`;
  return number(v);
}

function DailyValues({ items }: { items: SensorMetric[] }) {
  const shown = DAILY.flatMap(group => {
    const metric = group.metrics.map(name => items.find(item => item.metric === name)).find(item => item?.value != null && item.freshness !== "invalid");
    return metric ? [{ group, metric }] : [];
  });
  if (shown.length === 0) return <p className="text-sm text-muted">No daily numbers from Google Health yet.</p>;
  return <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{shown.map(({ group, metric }) => <li key={group.label} className="min-w-0 rounded-xl bg-inset p-4">
    <p className="text-sm text-body">{group.label}</p>
    <p className="mt-1 text-2xl font-bold tabular-nums text-ink">{dailyValue(group.label, metric)} {group.label !== "Sleep" && <span className="text-sm font-normal text-muted">{group.unit}</span>}</p>
    <p className="mt-1 text-xs text-muted">{metric.kind === "daily" && metric.local_date ? new Date(`${metric.local_date}T12:00:00Z`).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" }) : (metric.ended_at || metric.observed_at) ? <TimeAgo iso={(metric.ended_at || metric.observed_at)!} /> : null}</p>
  </li>)}</ul>;
}

export function LiveSensorPanel({ parentId, compact = false }: { parentId: string; compact?: boolean }) {
  // Remount for a different person; never retain the previous person's readings.
  return <SensorContent key={parentId} parentId={parentId} compact={compact} />;
}

function SensorContent({ parentId, compact }: { parentId: string; compact: boolean }) {
  const { data, loaded, failed } = useSensors(parentId);
  if (loaded && !data && !failed) return null;
  const metric = data?.live_heart_rate;
  const current = !failed && data?.connection_status === "available" && metric?.freshness === "fresh";
  const hasValue = metric?.value != null;
  const note = failed ? null : current ? "Updates every few seconds while the watch is nearby."
    : hasValue ? "No new reading lately. The watch may be charging or out of range."
    : "No reading from the watch yet.";
  return <section aria-label="Watch readings" className="min-w-0 overflow-hidden rounded-2xl border border-border bg-surface shadow-card [overflow-wrap:anywhere]" data-testid="live-sensors">
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-border px-5 py-3.5"><h2 className="flex items-center gap-2 text-base font-semibold text-ink"><Watch size={18} className="text-primary" aria-hidden="true" />Watch readings</h2><span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ${current ? "bg-primary-tint text-on-tint" : "bg-inset text-muted"}`}>{current && <Radio size={13} aria-hidden="true" />}{!loaded ? "Checking connection…" : current ? "Live · Fitbit BLE" : data?.connection_status === "not_linked" ? "Watch not linked" : hasValue ? "Last known reading" : "No live reading"}</span></div>
    <div className="p-5 [&>*:first-child]:mt-0">
    {failed && <p className="mt-3 text-sm text-watch-body" role="status">Can&apos;t refresh right now. Showing the last reading; trying again.</p>}
    {data?.connection_status === "not_linked" ? <p className="mt-3 text-sm text-body">No watch is connected yet. It can be linked with the wearer&apos;s permission.</p> : data && <>
      <div className="mt-4 flex flex-wrap items-end gap-x-4 gap-y-1"><div className="flex items-center gap-3"><HeartPulse size={30} className={current ? "text-primary" : "text-muted"} aria-hidden="true" /><div><p className="text-sm text-muted">{current ? "Heart rate now" : "Last heart rate"}</p><p className="text-4xl font-bold tabular-nums text-ink">{number(metric?.value)} <span className="text-base font-medium text-muted">bpm</span></p></div></div>{metric?.observed_at && <p className="pb-1 text-sm text-muted">Updated <TimeAgo iso={metric.observed_at} /></p>}</div>
      {note && <p className="mt-3 text-sm text-body">{note}</p>}
      {!compact && <PulseHistory points={data.recent_heart_rate} />}
      <p className="mt-3 text-xs text-muted">For staying in touch, not a medical alarm.</p>
    </>}
    {data && !compact && data.connection_status !== "not_linked" && <details className="mt-5 border-t border-border pt-4"><summary className="min-h-11 cursor-pointer py-2 font-semibold text-ink">Daily numbers from Google Health</summary><p className="mb-3 text-sm text-muted">These arrive after the watch syncs, usually a few times a day.</p><DailyValues items={data.cloud_metrics} /></details>}
    </div>
  </section>;
}

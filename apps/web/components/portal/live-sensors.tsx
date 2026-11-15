"use client";

import { useEffect, useState } from "react";
import { HeartPulse, Radio, Watch } from "lucide-react";
import { ApiError } from "@/lib/api";
import { experience, type SensorHealthReport, type SensorMetric } from "@/lib/experience-api";

function stamp(value?: string | null) {
  if (!value) return "No reading yet";
  return new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" });
}

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
    <div className="flex justify-between text-xs text-muted"><span>Recent received samples</span><span>{low}–{high} bpm · scaled to this window</span></div>
    <svg viewBox="0 0 600 88" role="img" aria-label={`Heart rate history: ${sorted.length} received samples, ${Math.min(...values)} to ${Math.max(...values)} beats per minute. Gaps mean missing samples.`} className="mt-2 h-24 w-full text-primary" preserveAspectRatio="none">{paths.filter(Boolean).map((d, i) => <path key={i} d={d} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" />)}</svg>
    <figcaption className="flex justify-between text-xs text-muted"><span>{new Date(start).toLocaleTimeString()}</span><span>{new Date(end).toLocaleTimeString()}</span></figcaption>
  </figure>;
}

function CloudValue({ metric }: { metric: SensorMetric }) {
  return <li className="min-w-0 rounded-xl border border-border bg-inset p-4">
    <p className="text-sm font-medium text-body">{metric.label}</p>
    <p className="mt-1 text-2xl font-bold tabular-nums text-ink">{number(metric.value)} <span className="text-sm font-normal text-muted">{metric.unit === "degC" ? "°C" : metric.unit === "count" ? "steps" : metric.unit}</span></p>
    <p className="mt-1 text-xs font-semibold text-muted">{metric.freshness === "fresh" ? "Recent report" : metric.freshness === "stale" ? "Older report" : metric.freshness === "invalid" ? "Reading unavailable" : "Not reported"}</p>
    <p className="mt-1 text-xs text-muted">{metric.kind === "daily" ? `Report date: ${metric.local_date ?? "unknown"}` : stamp(metric.ended_at || metric.observed_at)}</p>
    <p className="mt-2 text-xs text-muted">{metric.message}</p>
  </li>;
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
  return <section aria-label="Watch readings" className="rounded-2xl border border-border bg-surface p-5 sm:p-6" data-testid="live-sensors">
    <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="flex items-center gap-2 text-lg font-bold"><Watch size={20} className="text-primary" aria-hidden="true" />Watch readings</h2><span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ${current ? "bg-primary-tint text-on-tint" : "bg-inset text-muted"}`}>{current && <Radio size={13} aria-hidden="true" />}{!loaded ? "Checking connection…" : current ? "Live · Fitbit BLE" : data?.connection_status === "not_linked" ? "Watch not linked" : hasValue ? "Last known reading" : "No live reading"}</span></div>
    {failed && <p className="mt-3 text-sm text-watch-body" role="status">Refresh unavailable. Any value below is the last received reading. Retrying automatically.</p>}
    {data?.connection_status === "not_linked" ? <p className="mt-3 text-sm text-body">A watch has not been linked to this profile yet. Your care coordinator can connect it with your permission.</p> : data && <>
      <div className="mt-4 flex items-center gap-3"><HeartPulse size={30} className={current ? "text-primary" : "text-muted"} aria-hidden="true" /><div><p className="text-sm text-muted">{current ? "Heart rate" : "Last received heart rate"}</p><p className="text-4xl font-bold tabular-nums text-ink">{number(metric?.value)} <span className="text-base font-medium text-muted">bpm</span></p></div></div>
      <p className="mt-2 text-xs text-muted">Received: {stamp(metric?.observed_at)}</p>
      <p className="mt-2 text-sm text-body">{metric?.message}</p>
      {!compact && <PulseHistory points={data.recent_heart_rate} />}
      <p className="mt-3 text-xs text-muted">A current pulse is separate from resting heart rate. This view does not detect emergencies.</p>
    </>}
    {data && !compact && <details className="mt-5 border-t border-border pt-4"><summary className="min-h-11 cursor-pointer py-2 font-semibold text-ink">Google Health reports</summary><p className="mb-3 text-sm text-body">Cloud reports arrive after the watch syncs. Daily values, intervals and sleep sessions keep their original time periods.</p><ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{data.cloud_metrics.map(item => <CloudValue key={item.metric} metric={item} />)}</ul><p className="mt-3 text-xs text-muted">Daily trend update: {data.last_daily_sync_at ? stamp(data.last_daily_sync_at) : "Waiting for a linked source"}. {data.daily_sync_status === "unavailable" ? "The latest update could not complete." : ""}</p></details>}
  </section>;
}

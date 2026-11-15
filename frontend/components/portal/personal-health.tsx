"use client";

import { useCallback, useState, type FormEvent } from "react";
import { HeartPulse, Sprout, ShieldCheck } from "lucide-react";
import { experience, type BPReadingInfo, type MetricName, type PersonalMetric, type Symptom } from "@/lib/experience-api";
import { usePortalAction, usePortalData } from "@/lib/portal";
import { PortalButton, PortalCard, PortalMessage, inputClass } from "./ui";

const METRICS: Record<MetricName, { label:string; unit:string }> = {
  steps:{ label:"Daily steps", unit:"steps" }, resting_heart_rate:{ label:"Resting heart rate", unit:"bpm" }, sleep_minutes:{ label:"Sleep", unit:"minutes" }, systolic:{ label:"Systolic blood pressure", unit:"mmHg" }, diastolic:{ label:"Diastolic blood pressure", unit:"mmHg" },
};
const SYMPTOMS: { value:Symptom; label:string }[] = [
  { value:"chest_pain",label:"Chest pain" }, { value:"shortness_of_breath",label:"Shortness of breath" }, { value:"new_weakness",label:"New weakness" }, { value:"new_numbness",label:"New numbness" }, { value:"vision_change",label:"A change in vision" }, { value:"difficulty_speaking",label:"Difficulty speaking" }, { value:"sudden_severe_headache",label:"A sudden, severe headache" },
];
const localDateTime = () => { const now = new Date(); return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0,16); };

export function BloodPressureForm() {
  const action = usePortalAction();
  const [reading, setReading] = useState<BPReadingInfo | null>(null);
  const [measuredAt, setMeasuredAt] = useState(localDateTime);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const validated = String(values.get("device"));
    const body = { systolic:Number(values.get("systolic")), diastolic:Number(values.get("diastolic")), measured_at:new Date(String(values.get("time"))).toISOString(), device_validated:validated === "unknown" ? null : validated === "yes", symptoms_checked:values.get("reviewed") === "on", adult_nonpregnant:values.get("adult") === "on", symptoms:values.getAll("symptom") as Symptom[] };
    const saved = await action.run(`bp:${JSON.stringify(body)}`, action_id => experience.bloodPressure({ ...body,action_id }), "Your cuff reading is saved.");
    if (saved) { setReading(saved); form.reset(); setMeasuredAt(localDateTime()); }
  }
  return <>
    <header><p className="flex items-center gap-2 font-semibold text-primary"><HeartPulse size={20} aria-hidden="true" />From your blood-pressure cuff</p><h1 className="mt-2 text-3xl font-bold">Record your reading</h1><p className="mt-2 text-lg text-body">Enter the numbers shown on your monitor. Fitbit does not provide this reading.</p></header>
    {action.error && <PortalMessage error>{action.error}</PortalMessage>}
    {action.message && <PortalMessage>{action.message}</PortalMessage>}
    {reading && <Assessment reading={reading} />}
    <PortalCard><form onSubmit={submit} className="flex flex-col gap-5">
      <div className="grid grid-cols-2 gap-4"><label className="flex flex-col gap-2 font-semibold">Top number<span className="text-sm font-normal text-muted">Systolic · mmHg</span><input name="systolic" type="number" inputMode="numeric" required min={50} max={300} step={1} className={`${inputClass} text-3xl`} /></label><label className="flex flex-col gap-2 font-semibold">Bottom number<span className="text-sm font-normal text-muted">Diastolic · mmHg</span><input name="diastolic" type="number" inputMode="numeric" required min={20} max={200} step={1} className={`${inputClass} text-3xl`} /></label></div>
      <label className="flex flex-col gap-2 font-semibold">When was this measured?<input name="time" type="datetime-local" required value={measuredAt} onChange={event => setMeasuredAt(event.target.value)} className={inputClass} /><span className="text-sm font-normal text-muted">Time on this device. Enter a reading measured within the last 24 hours.</span></label>
      <label className="flex flex-col gap-2 font-semibold">Is your cuff a validated blood-pressure monitor?<select name="device" defaultValue="unknown" className={inputClass}><option value="unknown">I&apos;m not sure</option><option value="yes">Yes</option><option value="no">No</option></select></label>
      <fieldset className="rounded-2xl border border-border p-4"><legend className="px-2 text-lg font-bold">How are you feeling?</legend><p className="mb-3 text-body">Select any symptoms you are experiencing now.</p>{SYMPTOMS.map(symptom => <label key={symptom.value} className="flex min-h-12 items-center gap-3"><input type="checkbox" name="symptom" value={symptom.value} className="size-5 accent-primary" />{symptom.label}</label>)}<label className="mt-3 flex min-h-14 items-start gap-3 border-t border-border pt-4"><input type="checkbox" name="reviewed" required className="mt-1 size-5 shrink-0 accent-primary" /><span>I have reviewed the list. Unchecked symptoms are not present.</span></label></fieldset>
      <label className="flex min-h-12 items-start gap-3"><input type="checkbox" name="adult" className="mt-1 size-5 shrink-0 accent-primary" /><span>I am an adult and not pregnant.<span className="mt-1 block text-sm text-muted">The guidance has a limited scope. Leave unchecked if this does not apply.</span></span></label>
      <PortalButton type="submit" disabled={action.busy} className="min-h-16 text-xl">{action.busy ? "Saving…" : "Save my reading"}</PortalButton>
      <p className="text-sm text-muted">Recording a reading does not request an emergency response. If you are in immediate danger, contact local emergency services.</p>
    </form></PortalCard>
  </>;
}

function Assessment({ reading }: { reading:BPReadingInfo }) {
  const a = reading.assessment;
  return <section aria-label="Guidance at the time this blood-pressure reading was recorded" className="rounded-2xl border-2 border-watch-border bg-watch-bg p-5"><p className="mb-2 text-xs font-semibold uppercase tracking-wide text-watch-label">Guidance at time recorded</p><div className="flex items-start justify-between gap-3"><h2 className="text-xl font-bold text-watch-heading">{a.title}</h2><span className="shrink-0 text-lg font-bold text-watch-heading">{reading.systolic}/{reading.diastolic}</span></div><p className="mt-3 text-watch-body">{a.message}</p><p className="mt-3 font-semibold text-watch-heading">{a.action}</p><p className="mt-3 text-sm text-watch-body">Recorded {new Date(reading.measured_at).toLocaleString()} · manual cuff{reading.data_mode !== "live" ? ` · ${reading.data_mode}` : ""}</p></section>;
}

export function ReferenceForm({ parentId }: { parentId:string }) {
  const action = usePortalAction();
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const body = { metric:String(values.get("metric")) as MetricName, value:Number(values.get("value")), note:String(values.get("note")) };
    await action.run(`reference:${JSON.stringify(body)}`, () => experience.reference(parentId, body), "Your provisional reference is saved. Safety guidance has not changed.");
  }
  return <PortalCard title="A reference you know about yourself" description="Optional. This is your own provisional reference, not a medically validated target. It never overrides safety guidance.">
    {action.error && <PortalMessage error>{action.error}</PortalMessage>}{action.message && <PortalMessage>{action.message}</PortalMessage>}
    <form onSubmit={submit} className="mt-4 flex flex-col gap-4"><label className="flex flex-col gap-2 font-semibold">Measure<select name="metric" className={inputClass}>{Object.entries(METRICS).map(([key,value]) => <option key={key} value={key}>{value.label} ({value.unit})</option>)}</select></label><label className="flex flex-col gap-2 font-semibold">Your reference value<input name="value" type="number" inputMode="decimal" min={0} max={200000} step="any" required className={inputClass} /></label><label className="flex flex-col gap-2 font-semibold">A note <span className="text-sm font-normal text-muted">Optional</span><input name="note" maxLength={500} className={inputClass} /></label><PortalButton type="submit" variant="secondary" disabled={action.busy}>Save provisional reference</PortalButton></form>
  </PortalCard>;
}

function CoverageDetails({ metric }: { metric:PersonalMetric }) {
  const labels: Record<string,string> = {
    invalid:"Invalid records", outside_completed_window:"Outside the completed-day window",
    acute_day:"Days excluded for safety context", unverified_or_ineligible:"Unverified or ineligible readings",
    conflicting_timestamp:"Conflicting readings at the same time", duplicate:"Duplicate readings",
    conflicting_daily_value:"Conflicting daily values", duplicate_daily_value:"Duplicate daily values",
    too_close_in_time:"Readings too close together", insufficient_daily_coverage:"Days without enough qualifying readings",
  };
  const counts = Object.entries(metric.excluded_counts ?? {}).filter(([,count]) => count > 0);
  return <details className="mt-3 border-t border-border pt-3 text-sm"><summary className="min-h-9 cursor-pointer font-semibold text-primary">How days qualify</summary><p className="mt-2 text-muted">At least {metric.min_readings_per_day} qualifying reading{metric.min_readings_per_day === 1 ? "" : "s"} on each completed day.{(metric.metric === "systolic" || metric.metric === "diastolic") ? " Blood-pressure readings must be at least one minute apart and meet the app’s eligibility checks." : ""}</p>{counts.length > 0 && <ul className="mt-2 space-y-1 text-muted">{counts.map(([key,count]) => <li key={key}>{labels[key] || key.replaceAll("_", " ")}: {count}</li>)}</ul>}</details>;
}

function usual(metric: PersonalMetric) {
  const v = metric.median!;
  if (metric.metric === "sleep_minutes") return `${Math.floor(v / 60)}h ${String(Math.round(v % 60)).padStart(2, "0")}m`;
  return `${v.toLocaleString(undefined, { maximumFractionDigits: 0 })} ${METRICS[metric.metric].unit}`;
}

function LearningRow({ metric }: { metric: PersonalMetric }) {
  const label = METRICS[metric.metric].label;
  const progress = Math.min(metric.required_days, metric.eligible_days);
  const comparison = metric.comparison && metric.comparison.status !== "unavailable" ? metric.comparison.message : null;
  return <li className="flex flex-col gap-1 py-4 first:pt-0 last:pb-0">
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
      <h3 className="font-semibold text-ink">{label}</h3>
      {metric.status === "observed" && metric.median != null
        ? <p className="font-semibold text-ink">Usual: about {usual(metric)}</p>
        : metric.status === "paused" ? <p className="text-sm font-semibold text-muted">Paused</p>
        : <div className="flex items-center gap-3"><span role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={metric.required_days} aria-label={`${label}: ${metric.eligible_days} of ${metric.required_days} days`} className="h-2 w-24 overflow-hidden rounded-full bg-ring-track"><span className="block h-full rounded-full bg-primary" style={{ width: `${(progress / Math.max(1, metric.required_days)) * 100}%` }} /></span><span className="text-sm tabular text-muted">{metric.eligible_days} of {metric.required_days} days</span></div>}
    </div>
    {comparison && <p className="text-sm text-body">{comparison}</p>}
    {metric.declared_reference != null && <p className="text-sm text-muted">Your own reference: {metric.declared_reference} {METRICS[metric.metric].unit} (doesn&apos;t change safety guidance)</p>}
  </li>;
}

export function PersonalHealthPanel({ parentId }: { parentId:string }) {
  const fetcher = useCallback(() => experience.personalHealth(parentId), [parentId]);
  const result = usePortalData(fetcher);
  const data = result.data;
  const isBp = (m: PersonalMetric) => m.metric === "systolic" || m.metric === "diastolic";
  const hasBp = !!data && (data.blood_pressure.length > 0 || data.metrics.some(m => isBp(m) && m.eligible_days > 0));
  const rows = data ? data.metrics.filter(m => hasBp || !isBp(m)) : [];
  return <section className="min-w-0 overflow-hidden rounded-2xl border border-border bg-surface shadow-card" aria-labelledby="personal-health-heading">
    <header className="border-b border-border px-5 py-3.5"><h2 id="personal-health-heading" className="flex items-center gap-2 text-base font-semibold text-ink"><Sprout size={18} className="text-primary" aria-hidden="true" />Learning what is usual</h2><p className="text-sm text-muted">FarCare learns what&apos;s normal from about a week of readings, then compares new days with it.</p></header>
    <div className="flex flex-col gap-4 p-5">
    {result.loading && <p role="status" className="text-muted">Loading…</p>}
    {result.error && <PortalMessage error>{result.error}</PortalMessage>}
    {data && <>
      <ul className="divide-y divide-border">{rows.map(metric => <LearningRow key={metric.metric} metric={metric} />)}</ul>
      {!hasBp && <p className="flex items-center gap-2 text-sm text-muted"><HeartPulse size={17} aria-hidden="true" />Blood pressure: no cuff readings yet.</p>}
      {data.blood_pressure.length > 0 && <section className="flex flex-col gap-3"><h3 className="mt-2 text-lg font-bold">Recorded cuff readings</h3>{data.blood_pressure.slice(0,3).map(reading => <Assessment key={reading.reading_id} reading={reading} />)}</section>}
      <details className="border-t border-border pt-3 text-sm"><summary className="min-h-10 cursor-pointer py-2 font-semibold text-primary">How FarCare learns</summary>
        <div className="mt-2 flex flex-col gap-3 text-muted">
          <p>{data.explanation}</p>
          {data.metrics.map(metric => <div key={metric.metric}><p><strong className="text-body">{METRICS[metric.metric].label}:</strong> {metric.explanation} {metric.eligible_readings} readings in the last {metric.window_days} days.</p><CoverageDetails metric={metric} /></div>)}
          <p className="flex items-start gap-2"><ShieldCheck size={18} className="shrink-0" aria-hidden="true" /><span>Blood pressure comes from cuff readings entered by hand. This is not a diagnosis. {data.evidence.map((source,index) => <span key={source.url}>{index > 0 ? " · " : " "}<a href={source.url} target="_blank" rel="noreferrer" className="text-primary underline underline-offset-2">{source.title}</a></span>)}</span></p>
        </div>
      </details>
    </>}
    </div>
  </section>;
}

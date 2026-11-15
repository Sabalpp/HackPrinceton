"use client";

import { useCallback, type FormEvent, type ReactNode } from "react";
import { BellRing } from "lucide-react";
import { experience } from "@/lib/experience-api";
import { usePortalAction, usePortalData } from "@/lib/portal";
import { TimeAgo } from "@/components/ui/time";
import { PortalButton, PortalMessage } from "./ui";

const control = "min-h-11 shrink-0 rounded-xl border border-border bg-surface px-3 py-2 text-base text-ink focus:border-primary";

const MINUTES = [1, 2, 5, 10, 15];

/** The family's own heart-rate limits. Shown to the parent and to family the parent shares health with. */
export function HeartRateAlertCard({ parentId, name, self = false }: { parentId: string; name: string; self?: boolean }) {
  const fetcher = useCallback(() => experience.heartRateAlert(parentId), [parentId]);
  const result = usePortalData(fetcher);
  const action = usePortalAction();
  const data = result.data;
  if (!data) return null; // still loading, or health isn't shared with this viewer

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const f = new FormData(event.currentTarget);
    const body = {
      high_bpm: f.get("high_on") === "on" ? Number(f.get("high")) : null,
      low_bpm: f.get("low_on") === "on" ? Number(f.get("low")) : null,
      sustain_minutes: Number(f.get("minutes")),
      notify_family: f.get("notify_family") === "on",
      check_on_parent: f.get("check_on_parent") === "on",
      enabled: f.get("enabled") === "on",
    };
    const saved = await action.run(`hr-alert:${JSON.stringify(body)}`, action_id => experience.saveHeartRateAlert(parentId, { ...body, action_id }), next => next.enabled ? "Alert saved." : "Alerts are off.");
    if (saved) await result.refresh();
  }

  const on = data.enabled;
  return <section aria-labelledby="hr-alert-title" className="min-w-0 overflow-hidden rounded-2xl border border-border bg-surface shadow-card">
    <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-border px-5 py-3.5">
      <div className="min-w-0">
        <h2 id="hr-alert-title" className="flex items-center gap-2 text-base font-semibold text-ink"><BellRing size={18} className="text-primary" aria-hidden="true" />Heart-rate alerts</h2>
        <p className="text-sm text-muted">{self ? "Get a text if your heart rate stays outside limits you choose." : `Get a text if ${name}'s heart rate stays outside limits you choose.`}</p>
      </div>
      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${on ? "bg-primary-tint text-on-tint" : "bg-inset text-muted"}`}>{on ? "On" : "Off"}</span>
    </header>
    <form key={data.updated_at ?? "new"} onSubmit={save} className="flex flex-col gap-5 p-5">
      {action.error && <PortalMessage error>{action.error}</PortalMessage>}
      {action.message && <PortalMessage>{action.message}</PortalMessage>}
      <div className="grid gap-3 sm:grid-cols-2">
        <Limit name="high" label="Above" defaultOn={data.high_bpm != null || !data.configured} value={data.high_bpm ?? 120} min={60} max={220} />
        <Limit name="low" label="Below" defaultOn={data.low_bpm != null} value={data.low_bpm ?? 45} min={30} max={100} />
      </div>
      <label className="flex flex-wrap items-center gap-3 text-base text-body">For at least
        <select name="minutes" defaultValue={MINUTES.includes(data.sustain_minutes) ? data.sustain_minutes : 2} className={control}>{MINUTES.map(m => <option key={m} value={m}>{m} minute{m === 1 ? "" : "s"}</option>)}</select>
      </label>
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 font-semibold text-ink">When it happens</legend>
        <Check name="notify_family" defaultChecked={data.notify_family}>{self ? "Text my family who can see my health" : `Text family who can see ${name}'s health`}</Check>
        <Check name="check_on_parent" defaultChecked={data.check_on_parent}>
          {self ? "Ask me “Are you okay?”" : `Ask ${name} “Are you okay?”`}
          <span className="block text-sm text-muted">If there&apos;s no reply, FarCare asks a nearby helper to check. Helpers never see health numbers.</span>
        </Check>
        <Check name="enabled" defaultChecked={data.enabled || !data.configured}>Alerts on</Check>
      </fieldset>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <PortalButton type="submit" disabled={action.busy}>{action.busy ? "Saving…" : "Save alert"}</PortalButton>
        {data.last_alert_at && <p className="text-sm text-body">Last alert: {data.last_alert_bpm} bpm, <TimeAgo iso={data.last_alert_at} /></p>}
      </div>
      {!data.watch_linked && <p className="text-sm text-watch-body">No watch is linked yet, so alerts can&apos;t go off.</p>}
      <p className="text-sm text-muted">{data.updated_by_name ? `Set by ${data.updated_by_name}. ` : ""}These are your own limits, not medical advice. Short spikes and gaps in the readings are ignored. FarCare never calls emergency services.</p>
    </form>
  </section>;
}

function Limit({ name, label, defaultOn, value, min, max }: { name: string; label: string; defaultOn: boolean; value: number; min: number; max: number }) {
  return <div className="flex items-center gap-3 rounded-xl bg-inset px-4 py-3">
    <input id={`${name}_on`} name={`${name}_on`} type="checkbox" defaultChecked={defaultOn} className="size-5 shrink-0 accent-primary" />
    <label htmlFor={`${name}_on`} className="whitespace-nowrap font-semibold text-ink">{label}</label>
    <input name={name} type="number" inputMode="numeric" aria-label={`${label} limit in beats per minute`} defaultValue={value} min={min} max={max} step={1} className={`${control} w-24 tabular-nums`} />
    <span className="whitespace-nowrap text-sm text-muted">bpm</span>
  </div>;
}

function Check({ name, defaultChecked, children }: { name: string; defaultChecked: boolean; children: ReactNode }) {
  return <label className="flex min-h-11 items-start gap-3 text-base text-body"><input name={name} type="checkbox" defaultChecked={defaultChecked} className="mt-1 size-5 shrink-0 accent-primary" /><span>{children}</span></label>;
}

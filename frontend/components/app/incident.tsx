"use client";

import { useState } from "react";
import { Phone, ShieldAlert, ShieldCheck } from "lucide-react";
import { api, type CareRequest, type ParentProfile, type SafetyIncident } from "@/lib/api";
import { clockTime, escalationText, INCIDENT_STATUS, relationshipLabel } from "@/lib/format";
import { incidentNow } from "@/lib/summary";
import { useDashboard } from "@/lib/dashboard";
import { btn, SimulatedPill, StatusPill } from "@/components/farcare/ui";
import { Countdown, TimeAgo } from "@/components/ui/time";
import { toneToPill } from "./kit";

type Step = { at: string; text: string };

/** What happened so far, oldest first, built only from fields the API returns. */
function steps(i: SafetyIncident, reqs: CareRequest[], name: string, relOf: (id: string | null) => string): Step[] {
  const out: Step[] = [
    {
      at: i.opened_at,
      text:
        i.trigger === "fall.suspected"
          ? `Possible fall detected. FarCare texted ${name} to check they're okay.`
          : i.trigger === "family_concern"
            ? `Family asked FarCare to check on ${name}. FarCare texted ${name} to check they're okay.`
            : `${name} sent a message that may be an emergency.`,
    },
  ];
  const reason = escalationText(i, name);
  if (i.escalated_at && reason) out.push({ at: i.escalated_at, text: `${reason} Family and Care Circle alerted.` });
  for (const r of [...reqs].sort((a, b) => a.created_at.localeCompare(b.created_at))) {
    const who = `${r.responder_name ?? "A Care Circle member"}${relOf(r.responder_id)}`;
    out.push({ at: r.created_at, text: `Asked ${who} to go over.` });
    if (r.status === "accepted" || r.status === "completed")
      out.push({ at: r.updated_at, text: `${r.responder_name} said yes${r.estimated_arrival_minutes ? `, about ${r.estimated_arrival_minutes} min away` : ""}.` });
    if (r.status === "declined" || r.status === "expired") out.push({ at: r.updated_at, text: `${r.responder_name} couldn't go.` });
  }
  if (i.no_responder_available) out.push({ at: i.escalated_at ?? i.opened_at, text: "No one in the Care Circle is available." });
  if (i.resolved_at) {
    const how =
      i.resolution === "parent_ok"
        ? `${name} replied OK.`
        : i.resolution === "responder_confirmed"
          ? `A Care Circle member confirmed ${name} is okay.`
          : "The family marked it resolved.";
    out.push({ at: i.resolved_at, text: `${how} Alert closed.` });
  }
  return out.sort((a, b) => a.at.localeCompare(b.at));
}

export function IncidentPanel({
  incident,
  parent,
  requests,
  relationshipOf,
  compact = false,
}: {
  incident: SafetyIncident;
  parent: ParentProfile;
  /** Care requests linked to this incident (incident_id). */
  requests: CareRequest[];
  relationshipOf: (responderId: string | null) => string | undefined;
  compact?: boolean;
}) {
  const name = parent.display_name;
  const resolved = incident.status === "resolved";
  const latestReq = [...requests].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  const headline = resolved ? { title: `${name} is safe`, tone: "done" as const } : incidentNow(incident, latestReq, name);
  const urgent = headline.tone === "urgent";
  const status = INCIDENT_STATUS[incident.status];
  const rel = (id: string | null) => {
    const r = relationshipOf(id);
    return r ? ` (${relationshipLabel(r).toLowerCase()})` : "";
  };
  const list = steps(incident, requests, name, rel);
  const deadline = incident.status === "checking" ? incident.check_deadline : null;

  const frame = resolved
    ? "border-border bg-surface"
    : urgent
      ? "border-danger/30 bg-danger-tint"
      : incident.status === "checking"
        ? "border-watch-border bg-watch-bg"
        : "border-primary/25 bg-primary-tint";

  return (
    <article className={`flex flex-col gap-5 rounded-2xl border p-5 sm:p-6 ${frame}`}>
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2 text-sm font-semibold">
          <span className={`inline-flex items-center gap-1.5 ${resolved ? "text-good" : urgent ? "text-danger-ink" : "text-watch-label"}`}>
            {resolved ? <ShieldCheck size={18} aria-hidden="true" /> : <ShieldAlert size={18} aria-hidden="true" />}
            {incident.trigger === "fall.suspected"
              ? "Possible fall"
              : incident.trigger === "family_concern"
                ? "Family check-in"
                : "Message alert"}
          </span>
          <span className="text-muted" aria-hidden="true">
            ·
          </span>
          <span className="text-muted">
            Started <TimeAgo iso={incident.opened_at} />
          </span>
          {incident.data_mode === "simulated" && <SimulatedPill />}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className={`text-2xl font-bold ${urgent ? "text-danger-ink" : "text-ink"}`}>{headline.title}</h3>
          <StatusPill tone={toneToPill[status.tone]}>{status.label}</StatusPill>
        </div>
        {deadline && (
          <p className="font-semibold text-watch-label">
            Waiting for {name} to reply · <Countdown iso={deadline} fallback="no reply yet, alerting the Care Circle" />
          </p>
        )}
      </div>

      {!compact && (
        <div className="rounded-xl bg-surface/80 p-4">
          <h4 className="mb-3 text-xs font-bold uppercase tracking-wide text-muted">What happened ({name}&apos;s time)</h4>
          <ol className="flex flex-col">
            {list.map((s, idx) => {
              const last = idx === list.length - 1;
              return (
                <li key={idx} className="grid grid-cols-[4.5rem_1rem_1fr] gap-x-2">
                  <span className="tabular pt-0.5 text-sm text-muted">{clockTime(s.at, parent.timezone)}</span>
                  <span className="flex flex-col items-center" aria-hidden="true">
                    <span className={`mt-1.5 size-2.5 rounded-full ${last ? (resolved ? "bg-good" : "bg-sun") : "bg-border"}`} />
                    {!last && <span className="w-px flex-1 bg-border" />}
                  </span>
                  <span className={`pb-3 ${last ? "font-semibold text-ink" : "text-body"}`}>{s.text}</span>
                </li>
              );
            })}
          </ol>
          {incident.trigger === "fall.suspected" && typeof incident.confidence === "number" && (
            <p className="text-sm text-muted">
              Detector score {incident.confidence.toFixed(2)}: a sensor estimate, not a medical measurement.
            </p>
          )}
        </div>
      )}

      {!resolved && (
        <div className="flex flex-col gap-3">
          <ResolveControl incident={incident} name={name} emergencyNumber={parent.emergency_number} />
          <p className="text-sm text-body">
            If you think {name} is in danger, call local emergency services
            {parent.emergency_number ? ` (${parent.emergency_number} where ${name} lives)` : ""}. FarCare never calls
            them for you.
          </p>
        </div>
      )}
    </article>
  );
}

/** Two-step confirm so nobody closes an emergency with a stray tap. */
function ResolveControl({
  incident,
  name,
  emergencyNumber,
}: {
  incident: SafetyIncident;
  name: string;
  emergencyNumber: string | null;
}) {
  const { token, refresh } = useDashboard();
  const [step, setStep] = useState<"idle" | "confirm" | "saving">("idle");
  const [error, setError] = useState<string | null>(null);

  async function resolve() {
    setStep("saving");
    setError(null);
    try {
      await api.resolveIncident(token, incident.parent_id, incident.incident_id);
      refresh();
    } catch {
      setError("Couldn't close the alert. Check your connection and try again.");
      setStep("confirm");
    }
  }

  if (step === "idle") {
    return (
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={() => setStep("confirm")} className={btn.primary}>
          <ShieldCheck size={18} aria-hidden="true" />
          {name} is safe
        </button>
        {emergencyNumber && (
          <a href={`tel:${emergencyNumber}`} className={btn.outline}>
            <Phone size={18} aria-hidden="true" />
            <span className="tabular">Call {emergencyNumber}</span>
          </a>
        )}
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      <p className="font-semibold text-ink">Close this alert for everyone? Only if you know {name} is safe.</p>
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={resolve} disabled={step === "saving"} className={btn.primary}>
          {step === "saving" ? "Closing…" : "Yes, close the alert"}
        </button>
        <button type="button" onClick={() => setStep("idle")} disabled={step === "saving"} className={btn.outline}>
          Not yet
        </button>
      </div>
      {error && (
        <p role="alert" className="text-sm font-semibold text-danger-ink">
          {error}
        </p>
      )}
    </div>
  );
}

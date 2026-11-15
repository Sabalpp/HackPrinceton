import type { CareRequest, TimelineEvent } from "@/lib/api";
import { CATEGORY_LABEL, isRequestStopped, REQUEST_STATUS, requestStep } from "@/lib/format";
import { requestSentence } from "@/lib/summary";
import { StatusPill } from "@/components/farcare/ui";
import { TimeAgo } from "@/components/ui/time";
import { CATEGORY_ICON, toneToPill } from "./kit";

/** Four bars: who has said yes so far. The sentence carries the detail. */
export function Stepper({ request: r, parentName }: { request: CareRequest; parentName: string }) {
  const labels = [r.approval_basis === "emergency_policy" ? "Alert" : `${parentName} OK'd`, "Asked", "Said yes", "Done"];
  const step = requestStep(r.status);
  const stopped = isRequestStopped(r.status);
  const achieved = r.status === "accepted" || r.status === "completed";
  return (
    <ol className="grid grid-cols-4 gap-1.5" aria-label="Progress">
      {labels.map((label, i) => {
        const done = r.status === "completed" || i < step || (i === step && achieved);
        const current = i === step && !done;
        const bar = done ? "bg-primary" : current ? (stopped ? "bg-muted/40" : "bg-sun") : "bg-border";
        return (
          <li key={i} className="flex min-w-0 flex-col gap-1.5">
            <span className={`h-1.5 rounded-full ${bar}`} aria-hidden="true" />
            <span className={`truncate text-xs sm:text-sm ${done || current ? "font-medium text-ink" : "text-muted"}`}>
              {label}
              <span className="sr-only">{done ? " (done)" : current ? " (in progress)" : " (not yet)"}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function RequestCard({
  request: r,
  parentName,
  events,
  quiet = false,
}: {
  request: CareRequest;
  parentName: string;
  /** Timeline events for this request (ref_id), newest first. Shown when provided. */
  events?: TimelineEvent[];
  quiet?: boolean;
}) {
  const status = REQUEST_STATUS[r.status];
  const Icon = CATEGORY_ICON[r.request_type];
  const emergency = r.approval_basis === "emergency_policy";
  return (
    <article className={`flex flex-col gap-3 rounded-2xl border p-5 ${quiet ? "border-border bg-inset" : "border-border bg-surface"}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <span
            className={`grid size-11 shrink-0 place-items-center rounded-xl ${emergency ? "bg-danger-tint text-danger-ink" : "bg-primary-tint text-primary"}`}
            aria-hidden="true"
          >
            <Icon size={22} />
          </span>
          <div className="min-w-0">
            <h3 className="font-semibold text-ink">
              {CATEGORY_LABEL[r.request_type]}
              {r.requested_time && <span className="font-normal text-muted"> · {r.requested_time}</span>}
            </h3>
            <p className="text-sm text-muted">
              {emergency ? "From a safety alert" : `Asked by ${parentName}`} · <TimeAgo iso={r.created_at} />
            </p>
          </div>
        </div>
        <StatusPill tone={toneToPill[status.tone]}>{status.label}</StatusPill>
      </div>

      <p className={`text-lg font-semibold ${r.status === "accepted" ? "text-primary" : "text-ink"}`}>{requestSentence(r, parentName)}</p>
      {r.details && <p className="text-body">{r.details}</p>}
      {!quiet && <Stepper request={r} parentName={parentName} />}

      {events && events.length > 0 && (
        <details className="group rounded-xl bg-inset px-4 py-2">
          <summary className="cursor-pointer py-1 text-sm font-semibold text-primary">History ({events.length})</summary>
          <ol className="mt-2 flex flex-col gap-2 pb-2">
            {events.map((e) => (
              <li key={e.id} className="flex flex-wrap justify-between gap-x-3 text-sm">
                <span className="text-body">{e.summary}</span>
                <span className="text-muted">
                  <TimeAgo iso={e.created_at} />
                </span>
              </li>
            ))}
          </ol>
        </details>
      )}
    </article>
  );
}

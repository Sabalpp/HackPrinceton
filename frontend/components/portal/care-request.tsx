"use client";

import { Check, Clock3, HandHeart } from "lucide-react";
import type { CareRequest } from "@/lib/api";
import { CATEGORY_LABEL, REQUEST_STATUS } from "@/lib/format";
import { PortalButton } from "./ui";

export function ParentRequest({ request, busy, onDecision }: {
  request: CareRequest;
  busy: boolean;
  onDecision: (id: string, approve: boolean) => void;
}) {
  const pending = request.status === "awaiting_approval";
  const accepted = request.status === "accepted";
  const completed = request.status === "completed";
  return <article className={`rounded-3xl border p-5 sm:p-6 ${pending ? "border-watch-border bg-watch-bg" : "border-border bg-surface"}`}>
    <div className="flex items-start gap-3"><span className={`grid size-12 shrink-0 place-items-center rounded-2xl ${completed ? "bg-good-bg text-good" : "bg-primary-tint text-primary"}`}>{completed ? <Check size={26} aria-hidden="true" /> : accepted ? <HandHeart size={26} aria-hidden="true" /> : <Clock3 size={26} aria-hidden="true" />}</span><div><p className="text-sm font-bold uppercase tracking-wide text-muted">{pending ? "Your permission" : REQUEST_STATUS[request.status].label}</p><h3 className="text-xl font-bold text-ink">{CATEGORY_LABEL[request.request_type]}</h3></div></div>
    {request.details && <p className="mt-4 text-lg text-body">{request.details}</p>}
    {request.requested_time && <p className="mt-2 text-base text-muted">When: {request.requested_time}</p>}
    {pending ? <><p className="mt-4 text-lg text-ink">{request.responder_name ? `May we ask ${request.responder_name} to help?` : "May we ask someone in your Care Circle to help?"}</p><p className="mt-1 text-base text-body">They will receive your first name, this task, and its timing. Your health readings stay private.</p><div className="mt-5 grid gap-3 sm:grid-cols-2"><PortalButton className="min-h-16 text-xl" disabled={busy} onClick={() => onDecision(request.request_id, true)}>Yes, ask for help</PortalButton><PortalButton variant="secondary" className="min-h-16 text-xl" disabled={busy} onClick={() => onDecision(request.request_id, false)}>No, cancel</PortalButton></div></>
      : <p className="mt-4 text-lg font-semibold text-primary">{completed ? "This request is complete." : accepted ? `${request.responder_name || "Your helper"} has agreed to help.` : request.status === "sent" ? `Waiting for ${request.responder_name || "your helper"} to reply.` : REQUEST_STATUS[request.status].label}</p>}
    {request.estimated_arrival_minutes !== null && accepted && <p className="mt-2 text-body">Estimated arrival: {request.estimated_arrival_minutes} minutes</p>}
    {request.responder_note && <p className="mt-2 text-body">{request.responder_note}</p>}
  </article>;
}

"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Check, HandHeart, ShieldCheck } from "lucide-react";
import { RequireRole, useSession } from "@/lib/session";
import { experience, type ResponderTask } from "@/lib/experience-api";
import { usePortalAction, usePortalData } from "@/lib/portal";
import { CATEGORY_LABEL, REQUEST_STATUS } from "@/lib/format";
import { AgentStatusLine } from "./agent-status";
import { inputClass, PortalButton, PortalMessage, PortalShell } from "./ui";

export function ResponderScreen() { return <RequireRole roles={["responder"]}><ResponderContent /></RequireRole>; }

function ResponderContent() {
  const { session, logout } = useSession();
  const router = useRouter();
  const result = usePortalData(experience.responderTasks);
  const action = usePortalAction();
  const [logoutError, setLogoutError] = useState(false);
  const tasks = result.data?.tasks || [];
  const active = tasks.filter(task => task.status === "sent" || task.status === "accepted");
  const done = tasks.filter(task => task.status !== "sent" && task.status !== "accepted");
  async function reply(task: ResponderTask, decision: "accept"|"decline"|"complete", eta?: number, note?: string) {
    const payload = { decision, eta_minutes:eta, note:note || "" };
    const saved = await action.run(`reply:${task.request_id}:${JSON.stringify(payload)}`, action_id => experience.responderReply(task.request_id, { ...payload,action_id }), decision === "accept" ? "You’ve accepted. The care request is updated." : decision === "complete" ? "Thank you. The request is marked complete." : "Your reply is saved.");
    if (saved) await result.refresh();
  }
  return <PortalShell name={session!.person!.display_name} roleLabel="Trusted helper" preview={session!.demo_session} onLogout={() => { void logout().then(() => router.replace("/login")).catch(() => setLogoutError(true)); }}>
    <header><p className="font-semibold text-primary">A little help, close to home</p><h1 className="mt-2 text-4xl font-bold">Your care requests</h1><p className="mt-3 text-lg text-body">Choose what you can help with. Keep everyone in the loop as you go.</p></header>
    <p className="flex items-start gap-2 rounded-xl bg-primary-tint p-4 text-sm text-on-tint"><ShieldCheck size={20} className="shrink-0" aria-hidden="true" />You see only tasks assigned to you, without private health readings or family conversations.</p>
    {logoutError && <PortalMessage error>We couldn&apos;t sign you out. Try again.</PortalMessage>}
    {result.error && <PortalMessage error>{result.error} {result.data ? "The list below may be out of date." : ""}</PortalMessage>}
    {action.error && <PortalMessage error>{action.error}</PortalMessage>}
    {action.message && <PortalMessage>{action.message}</PortalMessage>}
    {result.loading && <p role="status" className="text-muted">Loading your assigned requests…</p>}
    {!result.loading && !result.data && <PortalButton onClick={() => void result.refresh()}>Try again</PortalButton>}
    {result.data && <>
      {active.length === 0 && <div className="rounded-3xl border border-dashed border-border bg-surface px-6 py-10 text-center"><span className="mx-auto grid size-16 place-items-center rounded-full bg-primary-tint text-primary"><HandHeart size={31} aria-hidden="true" /></span><h2 className="mt-4 text-2xl font-bold">Nothing assigned right now</h2><p className="mx-auto mt-2 max-w-md text-body">New requests will appear here after the parent has approved your membership and asked for your help.</p></div>}
      {active.map(task => <TaskCard key={`${task.request_id}:${task.status}`} task={task} busy={action.busy} onReply={(decision,eta,note) => void reply(task,decision,eta,note)} />)}
      {done.length > 0 && <details className="rounded-2xl border border-border bg-surface p-5"><summary className="min-h-10 cursor-pointer font-bold">Past requests ({done.length})</summary><div className="mt-4 flex flex-col gap-3">{done.map(task => <article key={task.request_id} className="rounded-xl bg-inset p-4"><p className="flex items-center gap-2 font-semibold"><Check size={18} className="text-primary" aria-hidden="true" />{CATEGORY_LABEL[task.request_type]} for {task.parent_first_name}</p><p className="mt-1 text-sm text-muted">{REQUEST_STATUS[task.status].label}</p></article>)}</div></details>}
      <p className="text-sm text-muted">This page refreshes every 10 seconds while it is open. In-app requests work here; phone messages require a connected messaging account.</p>
      <AgentStatusLine agent={result.data.agent} />
    </>}
  </PortalShell>;
}

function TaskCard({ task, busy, onReply }: { task:ResponderTask; busy:boolean; onReply:(decision:"accept"|"decline"|"complete",eta?:number,note?:string)=>void }) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const eta = String(fields.get("eta") || "");
    onReply(task.status === "sent" ? "accept" : "complete", eta ? Number(eta) : undefined, String(fields.get("note") || ""));
  }
  return <article className="overflow-hidden rounded-3xl border border-border bg-surface"><div className={`border-b border-border px-6 py-3 text-sm font-bold ${task.status === "accepted" ? "bg-primary-tint text-on-tint" : "bg-watch-bg text-watch-heading"}`}>{task.status === "accepted" ? "You’re helping" : "A request for you"}</div><div className="p-6"><p className="font-semibold text-primary">For {task.parent_first_name}</p><h2 className="mt-1 text-2xl font-bold">{CATEGORY_LABEL[task.request_type]}</h2><p className="mt-3 text-lg text-body">{task.details}</p>{task.requested_time && <p className="mt-2 font-medium text-muted">When: {task.requested_time}</p>}{task.status === "accepted" && task.estimated_arrival_minutes !== null && <p className="mt-2 text-body">Your estimated arrival: {task.estimated_arrival_minutes} minutes</p>}<form onSubmit={submit} className="mt-6 flex flex-col gap-4">{task.status === "sent" && <label className="flex flex-col gap-2 font-semibold">Arrival estimate <span className="text-sm font-normal text-muted">Minutes from now · optional</span><input name="eta" type="number" inputMode="numeric" min={1} max={1440} className={inputClass} /></label>}<label className="flex flex-col gap-2 font-semibold">{task.status === "accepted" ? "Completion note" : "A note for the parent"}<span className="text-sm font-normal text-muted">Optional · task details only</span><textarea name="note" rows={2} maxLength={500} className={inputClass} /></label><div className="grid gap-3 sm:grid-cols-2"><PortalButton type="submit" disabled={busy}>{task.status === "accepted" ? "Mark complete" : "Yes, I can help"}</PortalButton>{task.status === "sent" && <PortalButton variant="secondary" disabled={busy} onClick={event => onReply("decline", undefined, String(new FormData(event.currentTarget.form!).get("note") || ""))}>I can&apos;t this time</PortalButton>}</div></form></div></article>;
}

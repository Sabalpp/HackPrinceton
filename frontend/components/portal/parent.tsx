"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Check, HeartPulse, HandHeart, Settings2, ArrowRight, TriangleAlert } from "lucide-react";
import { RequireRole, useSession } from "@/lib/session";
import { experience } from "@/lib/experience-api";
import { usePortalAction, usePortalData } from "@/lib/portal";
import { CATEGORY_LABEL } from "@/lib/format";
import type { AssistanceCategory } from "@/lib/api";
import { isRequestOpen } from "@/lib/summary";
import { AgentStatusLine } from "./agent-status";
import { LiveSensorPanel } from "./live-sensors";
import { ParentRequest } from "./care-request";
import { BloodPressureForm, ReferenceForm } from "./personal-health";
import { inputClass, PortalBack, PortalButton, PortalCard, PortalMessage, PortalShell } from "./ui";

export function ParentScreen() { return <RequireRole roles={["parent"]}><ParentContent /></RequireRole>; }

function ParentContent() {
  const { session, logout } = useSession();
  const router = useRouter();
  const result = usePortalData(experience.parentHome);
  const action = usePortalAction();
  const [view, setView] = useState<"home"|"help"|"bp"|"settings"|"urgent">("home");
  const [logoutError, setLogoutError] = useState(false);
  const data = result.data;
  const person = session!.person!;
  const name = data?.parent.display_name || person.display_name;
  async function checkIn(response: "okay" | "help") {
    const saved = await action.run(`checkin:${response}`, id => experience.checkIn(id, response), response === "okay" ? "Your check-in is saved." : "Your request for urgent help is saved. Check the update below.");
    if (saved) { setView("home"); await result.refresh(); }
  }
  async function createHelp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const body = { request_type:String(fields.get("type")) as AssistanceCategory, details:String(fields.get("details")), requested_time:String(fields.get("when")) || undefined };
    const saved = await action.run(`help:${JSON.stringify(body)}`, action_id => experience.parentRequest({ ...body, action_id }), request => request.status === "awaiting_approval" ? "Request created. Confirm below before a helper is contacted." : "Your request is saved. Its status is shown below.");
    if (saved) { setView("home"); await result.refresh(); }
  }
  async function decision(id: string, approve: boolean) {
    const saved = await action.run(`decision:${id}:${approve}`, action_id => experience.parentDecision(id, action_id, approve ? "approve" : "cancel"), approve ? "Your permission is saved. See the request status below." : "Request cancelled.");
    if (saved) await result.refresh();
  }
  const changeView = (next: typeof view) => { action.clear(); setView(next); };
  const urgentAction = <button type="button" disabled={action.busy} onClick={() => changeView("urgent")} className="flex min-h-16 items-center justify-center gap-3 rounded-2xl border-2 border-danger bg-danger-light px-5 py-4 text-lg font-bold text-danger hover:bg-danger-lighter disabled:opacity-60"><TriangleAlert size={24} aria-hidden="true" />I need urgent help</button>;
  return <PortalShell name={name} roleLabel="My FarCare" preview={session!.demo_session} onLogout={() => { void logout().then(() => router.replace("/login")).catch(() => setLogoutError(true)); }}>
    {logoutError && <PortalMessage error>We couldn&apos;t sign you out. Please try again.</PortalMessage>}
    {result.error && <PortalMessage error>{result.error} {data ? "The information below may be out of date." : ""}</PortalMessage>}
    {action.error && <PortalMessage error>{action.error}</PortalMessage>}
    {action.message && <PortalMessage>{action.message}</PortalMessage>}
    {result.loading && <p role="status" className="text-xl text-muted">Loading your FarCare…</p>}
    {!result.loading && !data && <PortalButton onClick={() => void result.refresh()}>Try again</PortalButton>}
    {!data && view === "home" && urgentAction}
    {view === "urgent" && <section className="rounded-3xl border-2 border-danger bg-surface p-6 sm:p-8" aria-labelledby="urgent-title">
      <span className="mb-5 grid size-16 place-items-center rounded-2xl bg-danger-light text-danger"><TriangleAlert size={34} aria-hidden="true" /></span>
      <h1 id="urgent-title" className="text-3xl font-bold text-ink">Request urgent help from your care team?</h1>
      <p className="mt-4 text-lg text-body">Confirming starts or updates a safety alert and asks your authorized Care Circle for urgent help.</p>
      <p className="mt-4 rounded-2xl bg-danger-light p-4 text-lg font-semibold text-danger">If you are in immediate danger, contact local emergency services directly. FarCare never calls emergency services and cannot guarantee someone will respond.</p>
      <p className="mt-4 text-base text-muted">This is separate from an everyday help request. Opening this screen does not send an alert.</p>
      <div className="mt-6 grid gap-3"><button type="button" disabled={action.busy} onClick={() => void checkIn("help")} className="min-h-16 rounded-xl bg-danger px-5 py-4 text-xl font-bold text-white hover:brightness-95 disabled:opacity-60">{action.busy ? "Requesting help…" : "Yes, request urgent help"}</button><PortalButton variant="secondary" disabled={action.busy} onClick={() => changeView("home")} className="min-h-16 text-xl">Go back without sending</PortalButton></div>
    </section>}
    {data && <>
      {view !== "home" && view !== "urgent" && <PortalBack onClick={() => changeView("home")}>Back to my day</PortalBack>}
      {view === "home" && <>
        <header><p className="text-base font-semibold text-primary">Your day, at your pace</p><h1 className="mt-2 text-4xl font-bold tracking-tight sm:text-5xl">Hello, {name}.</h1><p className="mt-3 text-lg text-body">A simple way to check in and ask for a hand.</p></header>
        {data.active_incident && <PortalCard title="There is an open safety check" description="Let your care team know how you are."><div className="grid gap-3 sm:grid-cols-2"><PortalButton disabled={action.busy} onClick={() => void checkIn("okay")}>I&apos;m okay</PortalButton><PortalButton variant="secondary" disabled={action.busy} onClick={() => changeView("urgent")}>I need help now</PortalButton></div><p className="mt-4 text-sm text-muted">If you are in immediate danger, contact local emergency services. FarCare does not call them for you.</p></PortalCard>}
        <div className="grid gap-4">
          <button type="button" disabled={action.busy} onClick={() => void checkIn("okay")} className="flex min-h-32 items-center gap-5 rounded-3xl bg-primary p-6 text-left text-on-primary hover:bg-primary-hover disabled:opacity-60"><span className="grid size-16 shrink-0 place-items-center rounded-2xl bg-black/10"><Check size={35} strokeWidth={2.5} aria-hidden="true" /></span><span><span className="block text-3xl font-bold">I&apos;m OK</span><span className="mt-1 block text-lg text-on-primary/80">A quick check-in for today</span></span></button>
          <button type="button" onClick={() => changeView("help")} className="flex min-h-32 items-center gap-5 rounded-3xl border-2 border-border bg-surface p-6 text-left hover:border-primary"><span className="grid size-16 shrink-0 place-items-center rounded-2xl bg-coral-tint text-coral-ink"><HandHeart size={34} aria-hidden="true" /></span><span className="flex-1"><span className="block text-3xl font-bold">Ask for help</span><span className="mt-1 block text-lg text-body">Groceries, a ride, or some company</span></span><ArrowRight className="hidden shrink-0 text-primary sm:block" size={25} aria-hidden="true" /></button>
          <button type="button" onClick={() => changeView("bp")} className="flex min-h-24 items-center gap-4 rounded-3xl border border-border bg-surface p-6 text-left hover:border-primary"><HeartPulse size={29} className="shrink-0 text-primary" aria-hidden="true" /><span><span className="block text-xl font-bold">Record blood pressure</span><span className="block text-base text-muted">Enter a reading from your cuff</span></span></button>
        </div>
        {urgentAction}
        {data.requests.filter(isRequestOpen).length > 0 && <section aria-label="Your help requests" className="flex flex-col gap-4"><h2 className="text-2xl font-bold">Your help requests</h2>{data.requests.filter(isRequestOpen).map(request => <ParentRequest key={request.request_id} request={request} busy={action.busy} onDecision={(id, approve) => void decision(id, approve)} />)}</section>}
        {data.requests.some(request => !isRequestOpen(request)) && <details className="rounded-2xl border border-border bg-surface p-5"><summary className="min-h-10 cursor-pointer text-lg font-semibold">Past requests</summary><div className="mt-3 flex flex-col gap-3">{data.requests.filter(request => !isRequestOpen(request)).slice(0, 3).map(request => <ParentRequest key={request.request_id} request={request} busy={action.busy} onDecision={() => {}} />)}</div></details>}
        <LiveSensorPanel parentId={data.parent.parent_id} compact />
        <AgentStatusLine agent={data.agent} />
        <button type="button" onClick={() => changeView("settings")} className="inline-flex min-h-12 items-center gap-2 self-start rounded-lg px-2 text-base font-semibold text-primary hover:bg-primary-tint"><Settings2 size={21} aria-hidden="true" />My sharing & Care Circle</button>
      </>}
      {view === "help" && <><header><h1 className="text-3xl font-bold">What would help?</h1><p className="mt-2 text-lg text-body">You will choose whether to send the request next.</p></header><PortalCard><form onSubmit={createHelp} className="flex flex-col gap-5"><label className="flex flex-col gap-2 text-lg font-semibold">I would like help with<select name="type" className={inputClass} defaultValue="grocery_assistance">{Object.entries(CATEGORY_LABEL).filter(([key]) => key !== "urgent_check_in").map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></label><label className="flex flex-col gap-2 text-lg font-semibold">A few details<textarea name="details" required maxLength={1000} rows={3} placeholder="Tell your helper what you need." className={inputClass} /></label><label className="flex flex-col gap-2 text-lg font-semibold">When? <span className="text-sm font-normal text-muted">Optional</span><input name="when" maxLength={120} placeholder="For example, tomorrow morning" className={inputClass} /></label><PortalButton type="submit" disabled={action.busy} className="min-h-16 text-xl">{action.busy ? "Saving…" : "Review my request"}</PortalButton></form></PortalCard></>}
      {view === "bp" && <BloodPressureForm />}
      {view === "settings" && <><header><h1 className="text-3xl font-bold">Your choices</h1><p className="mt-2 text-lg text-body">You stay in control of your information and your helpers.</p></header><PortalCard title="What your family can see"><div className="flex flex-col gap-6">{data.sharing.length === 0 && <p className="text-muted">No family members are linked yet.</p>}{data.sharing.map(member => <div key={member.member_id}><h3 className="text-lg font-bold">{member.display_name}</h3><p className="text-sm text-muted">{member.relationship}</p>{(["share_health","share_checkins"] as const).map(field => <label key={field} className="mt-2 flex min-h-12 items-center gap-3 text-lg"><input type="checkbox" checked={member[field]} disabled={action.busy} className="size-6 accent-primary" onChange={event => { const value = event.target.checked; void action.run(`sharing:${member.member_id}:${field}:${value}`, () => experience.sharing({ member_id:member.member_id, share_health:member.share_health, share_checkins:member.share_checkins, [field]:value }), "Sharing preference saved.").then(saved => { if(saved) void result.refresh(); }); }} />{field === "share_health" ? "Health summaries" : "My check-ins"}</label>)}</div>)}</div></PortalCard><PortalCard title="Your trusted helpers"><div className="flex flex-col gap-5">{data.responders.length === 0 && <p className="text-muted">No helpers have been invited yet.</p>}{data.responders.map(member => <form key={`${member.responder_id}:${member.active}:${member.emergency_contact}`} onSubmit={event => { event.preventDefault(); const values = new FormData(event.currentTarget); const active = values.get("active") === "on", emergency = values.get("emergency") === "on"; void action.run(`helper:${member.responder_id}:${active}:${emergency}`, () => experience.approveResponder(member.responder_id, active, emergency), "Care Circle permission saved.").then(saved => { if(saved) void result.refresh(); }); }} className="rounded-xl border border-border p-4"><h3 className="text-lg font-bold">{member.display_name}</h3><p className="text-sm text-muted">{member.relationship} · {member.active ? "Approved" : "Waiting for your approval"}</p><label className="mt-3 flex min-h-12 items-center gap-3"><input name="active" type="checkbox" defaultChecked={member.active} className="size-5 accent-primary" />Allow this person to help me</label><label className="flex min-h-12 items-center gap-3"><input name="emergency" type="checkbox" defaultChecked={member.emergency_contact} className="size-5 accent-primary" />May be contacted for safety checks</label><PortalButton type="submit" variant="secondary" disabled={action.busy} className="mt-3">Save permission</PortalButton></form>)}</div></PortalCard><ReferenceForm parentId={data.parent.parent_id} /></>}
    </>}
  </PortalShell>;
}

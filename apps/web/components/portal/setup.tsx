"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Copy, MailPlus, UserRoundPlus } from "lucide-react";
import { RequireRole, useSession } from "@/lib/session";
import { experience, type InviteIssued } from "@/lib/experience-api";
import { usePortalAction } from "@/lib/portal";
import { CATEGORY_LABEL } from "@/lib/format";
import type { AssistanceCategory } from "@/lib/api";
import { inputClass, PortalButton, PortalCard, PortalMessage, PortalShell } from "./ui";

export function SetupScreen() { return <RequireRole roles={["child","parent"]}><SetupContent /></RequireRole>; }

function SetupContent() {
  const auth = useSession();
  const router = useRouter();
  const action = usePortalAction();
  const [issued, setIssued] = useState<InviteIssued | null>(null);
  const [inviteUrl, setInviteUrl] = useState("");
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const [logoutError, setLogoutError] = useState(false);
  const session = auth.session!;
  async function keepInvite(value: InviteIssued | undefined) {
    if (!value) return;
    setIssued(value); setInviteUrl(`${window.location.origin}/join#token=${encodeURIComponent(value.invitation_token)}`); setCopied(false); setCopyError(false);
    await auth.refresh();
  }
  async function createParent(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const body = { display_name:String(fields.get("name")), timezone:String(fields.get("timezone")), country_code:String(fields.get("country")) as "NP"|"US"|"OTHER" };
    await keepInvite(await action.run(`create:${JSON.stringify(body)}`, action_id => experience.createParent({ ...body,action_id }), "Parent profile created. Share the private invitation so they can choose their permissions."));
  }
  async function inviteHelper(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const body = { parent_id:String(fields.get("parent")), role:"responder" as const, display_name:String(fields.get("name")), relationship:String(fields.get("relationship")), categories:fields.getAll("category") as AssistanceCategory[], emergency_contact:fields.get("emergency") === "on" };
    await keepInvite(await action.run(`invite:${JSON.stringify(body)}`, action_id => experience.invite({ ...body,action_id }), "Helper invitation created. The parent will approve membership after they join."));
  }
  return <PortalShell name={session.person!.display_name} roleLabel="Family setup" preview={session.demo_session} onLogout={() => { void auth.logout().then(() => router.replace("/login")).catch(() => setLogoutError(true)); }}>
    <header><p className="font-semibold text-primary">Set up once, stay connected</p><h1 className="mt-2 text-4xl font-bold">Bring your circle together.</h1><p className="mt-3 text-lg text-body">Invite your parent first. They decide what to share and which helpers to trust.</p></header>
    {logoutError && <PortalMessage error>We couldn&apos;t sign you out. Please try again.</PortalMessage>}
    {action.error && <PortalMessage error>{action.error}</PortalMessage>}{action.message && <PortalMessage>{action.message}</PortalMessage>}
    {issued && <PortalCard title={`An invitation for ${issued.display_name}`} description="This private link grants access to this invitation. Share it only with the intended person."><label className="flex flex-col gap-2 text-sm font-semibold">Private invitation link<input readOnly value={inviteUrl} onFocus={event => event.currentTarget.select()} className={`${inputClass} text-sm`} /></label><PortalButton variant="secondary" className="mt-4" onClick={() => { void navigator.clipboard.writeText(inviteUrl).then(() => { setCopied(true); setCopyError(false); }).catch(() => setCopyError(true)); }}><Copy size={18} aria-hidden="true" />{copied ? "Copied" : "Copy invitation"}</PortalButton>{copyError && <p role="alert" className="mt-2 text-sm text-body">Select the link above and copy it from your browser.</p>}<p className="mt-3 text-sm text-muted">Expires {new Date(issued.expires_at).toLocaleString()}. Keep this page open until you have copied the link.</p></PortalCard>}
    {session.person!.role === "child" && <PortalCard title="1. Invite your parent" description="Their health and check-ins stay private until they accept and choose to share."><form onSubmit={createParent} className="flex flex-col gap-4"><label className="flex flex-col gap-2 font-semibold">Parent&apos;s name<input name="name" autoComplete="off" required maxLength={80} className={inputClass} /></label><label className="flex flex-col gap-2 font-semibold">Parent&apos;s time zone<input name="timezone" list="parent-timezones" placeholder="Asia/Kathmandu" required maxLength={64} className={inputClass} /><datalist id="parent-timezones"><option value="Asia/Kathmandu">Nepal</option><option value="Asia/Kolkata">India</option><option value="America/New_York">US Eastern</option><option value="America/Chicago">US Central</option><option value="America/Los_Angeles">US Pacific</option><option value="Europe/London">United Kingdom</option></datalist><span className="text-sm font-normal text-muted">Used to group readings and show their local time.</span></label><label className="flex flex-col gap-2 font-semibold">Country<select name="country" defaultValue="OTHER" className={inputClass}><option value="NP">Nepal</option><option value="US">United States</option><option value="OTHER">Another country</option></select></label><PortalButton type="submit" disabled={action.busy}><UserRoundPlus size={20} aria-hidden="true" />Create parent invitation</PortalButton></form></PortalCard>}
    {(session.parent_ids ?? []).length > 0 && <PortalCard title="2. Invite a trusted helper" description="Your parent must approve the helper. Health readings are never included with assigned tasks."><form onSubmit={inviteHelper} className="flex flex-col gap-4"><label className="flex flex-col gap-2 font-semibold">Parent<select name="parent" className={inputClass}>{(session.parent_ids ?? []).map((id,index) => <option key={id} value={id}>{(session.parent_ids ?? []).length === 1 ? "Your linked parent" : `Linked parent ${index + 1}`}</option>)}</select></label><label className="flex flex-col gap-2 font-semibold">Helper&apos;s name<input name="name" required maxLength={80} className={inputClass} /></label><label className="flex flex-col gap-2 font-semibold">Relationship<input name="relationship" required maxLength={40} placeholder="Neighbor, friend, relative…" className={inputClass} /></label><fieldset><legend className="mb-2 font-semibold">Kinds of help</legend><div className="grid gap-1 sm:grid-cols-2">{Object.entries(CATEGORY_LABEL).map(([key,label]) => <label key={key} className="flex min-h-11 items-center gap-2 text-sm"><input name="category" value={key} type="checkbox" className="size-5 accent-primary" />{label}</label>)}</div></fieldset><label className="flex min-h-12 items-start gap-3"><input name="emergency" type="checkbox" className="mt-1 size-5 shrink-0 accent-primary" /><span>Ask the parent to approve this helper for safety checks too.</span></label><PortalButton type="submit" disabled={action.busy}><MailPlus size={20} aria-hidden="true" />Create helper invitation</PortalButton></form></PortalCard>}
    <Link href={session.person!.role === "parent" ? "/parent" : "/"} className="min-h-12 self-start rounded-lg px-2 py-3 font-semibold text-primary hover:bg-primary-tint">Return to my FarCare</Link>
  </PortalShell>;
}

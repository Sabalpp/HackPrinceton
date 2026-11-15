"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, HandHeart, Heart, Users } from "lucide-react";
import { useSession, roleHome } from "@/lib/session";
import { ApiError } from "@/lib/api";
import { experience, type InvitationPreview, type Session } from "@/lib/experience-api";
import { AuthFrame, inputClass, PortalButton, PortalMessage } from "./ui";

const home = (s: Session) => s.person?.role === "child" && (s.parent_ids ?? []).length === 0 ? "/setup" : s.person ? roleHome(s.person.role) : "/login";
const DEMO_ROLES = [
  { role: "parent", label: "Parent", Icon: Heart, who: "Check in, ask for help, choose who sees what" },
  { role: "child", label: "Family", Icon: Users, who: "See how your parent is doing from far away" },
  { role: "responder", label: "Helper", Icon: HandHeart, who: "A nearby neighbor who accepts tasks" },
] as const;
const failure = (e: unknown) => e instanceof ApiError ? e.message : "We couldn’t complete that. Please try again.";

export function LoginScreen() {
  const auth = useSession();
  const router = useRouter();
  const [registering, setRegistering] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    setBusy(true); setError(null);
    try {
      const email = String(fields.get("email")), password = String(fields.get("password"));
      const session = registering ? await auth.register({ email, password, display_name: String(fields.get("name")), share_health:false, share_checkins:false }) : await auth.login(email, password);
      router.replace(home(session));
    } catch (err) { setError(failure(err)); } finally { setBusy(false); }
  }
  async function demo(role: "child" | "parent" | "responder") {
    setBusy(true); setError(null);
    try { router.replace(home(await auth.demo(role))); }
    catch (err) { setError(failure(err)); } finally { setBusy(false); }
  }
  return <AuthFrame title={registering ? "Bring your family closer" : "Welcome back"} description={registering ? "Create your family account, then invite your parent to choose what they share." : "Sign in to the place your family comes together."}>
    {auth.session?.authenticated && <div className="mb-5 rounded-xl bg-primary-tint p-4"><p className="text-on-tint">Signed in as {auth.session.person?.display_name}.</p><Link href={home(auth.session)} className="mt-2 inline-flex items-center gap-1 font-bold text-primary">Open my FarCare <ArrowRight size={17} aria-hidden="true" /></Link></div>}
    {(error || auth.error) && <div className="mb-5"><PortalMessage error>{error || auth.error}</PortalMessage></div>}
    <form onSubmit={submit} aria-busy={busy} className="flex flex-col gap-4">
      {registering && <label className="flex flex-col gap-1.5 font-semibold">Your name<input name="name" autoComplete="name" required maxLength={80} className={inputClass} /></label>}
      <label className="flex flex-col gap-1.5 font-semibold">{registering ? "Email" : "Email or username"}<input name="email" type={registering ? "email" : "text"} inputMode={registering ? "email" : "text"} autoCapitalize="none" spellCheck={false} autoComplete="username" required maxLength={254} className={inputClass} /></label>
      <label className="flex flex-col gap-1.5 font-semibold">Password<input name="password" type="password" autoComplete={registering ? "new-password" : "current-password"} required minLength={registering ? 12 : undefined} maxLength={128} className={inputClass} />{registering && <span className="text-sm font-normal text-muted">Use at least 12 characters.</span>}</label>
      {registering && <p className="text-sm leading-relaxed text-muted">This creates a family member account. Parents and helpers join through a private invitation; their permissions are not selected here.</p>}
      <PortalButton type="submit" disabled={busy || auth.loading} className="mt-1 w-full">{busy ? "Please wait…" : registering ? "Create family account" : "Sign in"}<ArrowRight size={19} aria-hidden="true" /></PortalButton>
    </form>
    {auth.session?.registration_available && <p className="mt-5 text-center text-sm text-muted">{registering ? "Already have an account?" : "Setting up your family?"} <button type="button" onClick={() => { setRegistering(!registering); setError(null); }} className="min-h-11 font-bold text-primary hover:underline">{registering ? "Sign in" : "Create an account"}</button></p>}
    <p className="mt-2 text-center text-sm"><Link href="/join" className="inline-flex min-h-11 items-center rounded-lg px-2 font-semibold text-primary hover:underline">I have an invitation</Link></p>
    {auth.session?.demo_available && <section aria-labelledby="who-title" className="mt-7 border-t border-border pt-5"><h2 id="who-title" className="text-lg font-bold text-ink">Who are you?</h2><p className="mb-3 mt-0.5 text-sm text-muted">Try FarCare as one of our sample family. Each role sees its own screen.</p><div className="flex flex-col gap-2">{DEMO_ROLES.map(({ role,label,Icon,who }) => <button key={role} type="button" aria-label={label} disabled={busy} onClick={() => void demo(role)} className="flex min-h-16 items-center gap-4 rounded-xl border border-border bg-surface px-4 py-3 text-left shadow-card hover:border-primary disabled:opacity-50"><span className="grid size-11 shrink-0 place-items-center rounded-xl bg-primary-tint text-primary"><Icon size={22} aria-hidden="true" /></span><span className="min-w-0 flex-1"><span className="block font-bold text-ink">{label}</span><span className="block text-sm text-muted">{who}</span></span><ArrowRight size={18} className="shrink-0 text-primary" aria-hidden="true" /></button>)}</div></section>}
  </AuthFrame>;
}

export function JoinScreen() {
  const auth = useSession();
  const router = useRouter();
  const [token, setToken] = useState("");
  const [invitation, setInvitation] = useState<InvitationPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function lookup(value: string) {
    setBusy(true); setError(null);
    try { setInvitation(await experience.invitation(value)); setToken(value); }
    catch (err) { setError(failure(err)); } finally { setBusy(false); }
  }
  useEffect(() => {
    const value = new URLSearchParams(window.location.hash.slice(1)).get("token");
    let alive = true;
    if (value) void experience.invitation(value).then(
      info => { if (alive) { setInvitation(info); setToken(value); } },
      err => { if (alive) setError(failure(err)); },
    );
    return () => { alive = false; };
  }, []);
  async function join(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    setBusy(true); setError(null);
    try {
      const session = await auth.register({ email:String(values.get("email")), password:String(values.get("password")), display_name:String(values.get("name")), invitation_token:token, share_health:values.get("health") === "on", share_checkins:values.get("checkins") === "on" });
      window.history.replaceState(null, "", "/join");
      router.replace(home(session));
    } catch (err) { setError(failure(err)); } finally { setBusy(false); }
  }
  return <AuthFrame title={invitation ? `You’re invited, ${invitation.display_name || "welcome"}` : "Join your Care Circle"} description={invitation ? `${invitation.invited_by} invited you to FarCare as ${invitation.role === "parent" ? "a parent" : "a trusted helper"}.` : "Enter the 8-digit code your family shared, or open your private invitation link."}>
    {error && <div className="mb-5"><PortalMessage error>{error}</PortalMessage></div>}
    {!invitation ? <form onSubmit={event => { event.preventDefault(); void lookup(token.trim()); }} aria-busy={busy} className="flex flex-col gap-4"><label className="flex flex-col gap-2 font-semibold">Invitation code<input required type="text" inputMode="numeric" autoComplete="one-time-code" autoCapitalize="none" spellCheck={false} placeholder="1234 5678" maxLength={128} value={token} onChange={event => setToken(event.target.value)} className={`${inputClass} font-mono text-2xl tracking-wider`} /></label><p className="text-sm text-muted">Spaces are okay. If the code expired, ask for a new one.</p><PortalButton type="submit" disabled={busy}>{busy ? "Checking…" : "Check invitation"}</PortalButton></form>
      : <form onSubmit={join} aria-busy={busy} className="flex flex-col gap-4"><label className="flex flex-col gap-1.5 font-semibold">Your name<input name="name" defaultValue={invitation.display_name || ""} autoComplete="name" required maxLength={80} className={inputClass} /></label><label className="flex flex-col gap-1.5 font-semibold">Email<input name="email" type="email" inputMode="email" autoCapitalize="none" spellCheck={false} autoComplete="username" required maxLength={254} className={inputClass} /></label><label className="flex flex-col gap-1.5 font-semibold">Create a password<input name="password" type="password" autoComplete="new-password" required minLength={12} maxLength={128} className={inputClass} /><span className="text-sm font-normal text-muted">At least 12 characters.</span></label>
        {invitation.role === "parent" ? <fieldset className="rounded-2xl border border-border bg-surface p-4"><legend className="px-1 font-bold">You choose what to share</legend><p className="mb-3 text-sm text-muted">Optional. You can change these choices later.</p><label className="flex min-h-12 items-start gap-3 py-2"><input type="checkbox" name="health" className="mt-1 size-5 accent-primary" /><span>Share my health summaries with my family</span></label><label className="flex min-h-12 items-start gap-3 py-2"><input type="checkbox" name="checkins" className="mt-1 size-5 accent-primary" /><span>Share my check-ins with my family</span></label></fieldset> : <p className="rounded-xl bg-inset p-4 text-sm text-body">The parent will approve your Care Circle membership. You will see only tasks assigned to you, without private health readings.</p>}
        <PortalButton type="submit" disabled={busy}>{busy ? "Creating your account…" : "Accept invitation & create account"}</PortalButton></form>}
  </AuthFrame>;
}

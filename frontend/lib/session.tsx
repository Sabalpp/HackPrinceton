"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { ApiError, setSessionCsrf, type Schemas } from "./api";
import { experience, type Registration, type Session } from "./experience-api";

type Role = Schemas["PersonRole"];
export const roleHome = (role: Role) => role === "parent" ? "/parent" : role === "responder" ? "/respond" : "/";
type SessionContext = {
  session: Session | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  login: (email: string, password: string) => Promise<Session>;
  register: (input: Registration) => Promise<Session>;
  demo: (role: "child" | "parent" | "responder") => Promise<Session>;
  logout: () => Promise<void>;
};
const Context = createContext<SessionContext | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const accept = useCallback((next: Session) => {
    ++generation.current;
    setSessionCsrf(next.csrf_token ?? null);
    setSession(next);
    setError(null);
    setLoading(false);
    return next;
  }, []);
  const refresh = useCallback(() => {
    const ticket = ++generation.current;
    return experience.session().then(
      next => { if(ticket === generation.current) accept(next); },
      err => { if(ticket === generation.current) { setSession(null); setSessionCsrf(null); setError(err instanceof ApiError ? err.message : "We could not connect to FarCare."); } },
    ).finally(() => { setLoading(false); });
  }, [accept]);
  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    const expired = () => {
      ++generation.current;
      setSessionCsrf(null);
      setSession(null);
      setError(null);
      void refresh();
    };
    window.addEventListener("farcare:session-expired", expired);
    return () => window.removeEventListener("farcare:session-expired", expired);
  }, [refresh]);
  const value = useMemo<SessionContext>(() => ({
    session, loading, error, refresh,
    login: async (email, password) => accept(await experience.login(email, password)),
    register: async (input) => accept(await experience.register(input)),
    demo: async (role) => accept(await experience.demo(role)),
    logout: async () => {
      await experience.logout();
      setSessionCsrf(null);
      setSession(null);
      await refresh();
    },
  }), [session, loading, error, refresh, accept]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useSession() {
  const value = useContext(Context);
  if (!value) throw new Error("SessionProvider is required");
  return value;
}

export function RequireRole({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const { session, loading, error, refresh } = useSession();
  const router = useRouter();
  const person = session?.person;
  const allowed = !!session?.authenticated && !!person && roles.includes(person.role);
  useEffect(() => {
    if (loading || error || allowed) return;
    router.replace(session?.authenticated && person ? roleHome(person.role) : "/login");
  }, [loading, error, allowed, session?.authenticated, person, router]);
  if (error) return <main className="mx-auto flex min-h-screen max-w-lg flex-col justify-center gap-4 p-6"><h1 className="text-2xl font-bold">Couldn&apos;t connect to FarCare</h1><p role="alert" className="text-body">{error}</p><button type="button" onClick={() => void refresh()} className="min-h-12 rounded-xl bg-primary px-5 font-bold text-on-primary">Try again</button></main>;
  if (!allowed) return <main className="grid min-h-screen place-items-center p-6"><p role="status" className="text-lg text-muted">Opening your FarCare…</p></main>;
  return <>{children}</>;
}

export function LinkedFamily({ children }: { children:ReactNode }) {
  const { session } = useSession();
  const router = useRouter();
  const needsSetup = session?.person?.role === "child" && (session.parent_ids ?? []).length === 0;
  useEffect(() => { if (needsSetup) router.replace("/setup"); }, [needsSetup,router]);
  return needsSetup ? <p role="status" className="p-8 text-muted">Opening family setup…</p> : <>{children}</>;
}

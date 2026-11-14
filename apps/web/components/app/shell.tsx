"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { Activity, FlaskConical, HandHeart, HeartPulse, House, ShieldAlert, Users, type LucideIcon } from "lucide-react";
import { useDashboard, type Connection } from "@/lib/dashboard";
import { isRequestOpen } from "@/lib/summary";
import { Logo, SidebarFrame } from "@/components/farcare/sidebar";

const CONNECTION: Record<Connection, { label: string; dot: string }> = {
  live: { label: "Live", dot: "bg-primary animate-pulse" },
  connecting: { label: "Connecting…", dot: "bg-sun" },
  reconnecting: { label: "Reconnecting…", dot: "bg-sun" },
  offline: { label: "Offline", dot: "bg-muted" },
  mock: { label: "Demo data", dot: "bg-sun" },
};

type NavItem = { href: string; label: string; Icon: LucideIcon; badge?: { count: number; tone: "danger" | "primary" } };

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { state, connection, lastEvent } = useDashboard();
  const o = state.phase === "ready" ? state.overview : null;
  const alerts = o?.active_incidents.length ?? 0;
  const open = o?.recent_requests.filter(isRequestOpen).length ?? 0;

  const nav: NavItem[] = [
    { href: "/", label: "Today", Icon: House },
    { href: "/health", label: "Health", Icon: HeartPulse },
    { href: "/requests", label: "Help requests", Icon: HandHeart, badge: open ? { count: open, tone: "primary" } : undefined },
    { href: "/safety", label: "Safety alerts", Icon: ShieldAlert, badge: alerts ? { count: alerts, tone: "danger" } : undefined },
    { href: "/activity", label: "Activity", Icon: Activity },
    { href: "/circle", label: "Care Circle", Icon: Users },
  ];
  const active = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));
  const c = CONNECTION[connection];

  return (
    <div className="flex min-h-screen flex-1 flex-wrap bg-bg">
      <SidebarFrame>
        <div className="flex items-center justify-between gap-3">
          <Logo />
          <span className="inline-flex items-center gap-2 whitespace-nowrap rounded-full bg-inset px-2.5 py-1 text-xs font-semibold text-body" role="status">
            <span className={`size-2 rounded-full ${c.dot}`} aria-hidden="true" />
            {c.label}
          </span>
        </div>

        <nav aria-label="Main">
          <ul className="flex flex-wrap gap-1 md:flex-col">
            {nav.map(({ href, label, Icon, badge }) => (
              <li key={href}>
                <Link
                  href={href}
                  aria-current={active(href) ? "page" : undefined}
                  className={`flex min-h-11 items-center gap-3 rounded-[10px] px-3 ${
                    active(href) ? "bg-primary-tint font-semibold text-primary" : "text-body hover:bg-inset"
                  }`}
                >
                  <Icon size={19} aria-hidden="true" />
                  <span className="flex-1">{label}</span>
                  {badge && (
                    <span
                      className={`grid min-w-6 place-items-center rounded-full px-1.5 text-xs font-bold text-white ${
                        badge.tone === "danger" ? "bg-danger" : "bg-primary"
                      }`}
                    >
                      {badge.count}
                      <span className="sr-only"> {badge.tone === "danger" ? "active alerts" : "open"}</span>
                    </span>
                  )}
                </Link>
              </li>
            ))}
          </ul>
        </nav>

        <div className="mt-auto flex flex-col gap-3">
          {o && (
            <div className="rounded-2xl border border-border bg-inset p-4">
              <p className="text-sm text-muted">Caring for</p>
              <p className="text-lg font-semibold text-ink">{o.parent.display_name}</p>
              <p className="text-sm text-muted">{o.parent.timezone.split("/").pop()?.replaceAll("_", " ")}</p>
            </div>
          )}
          <Link
            href="/demo"
            aria-current={active("/demo") ? "page" : undefined}
            className={`flex min-h-11 items-center gap-2 rounded-[10px] border border-dashed px-3 text-sm font-semibold ${
              active("/demo") ? "border-primary bg-primary-tint text-primary" : "border-border text-muted hover:border-primary hover:text-primary"
            }`}
          >
            <FlaskConical size={16} aria-hidden="true" />
            Demo controls
          </Link>
          <details className="text-sm text-muted">
            <summary className="cursor-pointer rounded px-1 py-1 hover:text-ink">Concept screens</summary>
            <ul className="mt-1 flex flex-col gap-1 pl-2">
              {[
                ["/concept", "Daily call dashboard"],
                ["/call", "Live call"],
                ["/parent", "Parent phone"],
                ["/phone/digest", "Morning digest"],
                ["/setup", "Band setup"],
              ].map(([href, label]) => (
                <li key={href}>
                  <Link href={href} className="hover:text-primary hover:underline">
                    {label}
                  </Link>
                </li>
              ))}
            </ul>
          </details>
        </div>
      </SidebarFrame>

      <main className="min-w-0 max-w-[1180px] flex-[999_1_560px] px-4 py-6 sm:px-8 md:px-10 md:py-8">
        {/* Screen readers hear each live update once. */}
        <p className="sr-only" aria-live="polite">
          {lastEvent?.summary ?? ""}
        </p>
        {children}
        <footer className="mt-12 border-t border-border pt-5 text-sm text-muted">
          FarCare is a hackathon prototype, not a medical device. It doesn&apos;t diagnose anything and never contacts
          emergency services itself. People and sensor data in the demo are synthetic and labeled simulated.
        </footer>
      </main>
    </div>
  );
}

/** Wraps page content: shows loading / error until the dashboard store is ready. */
export function Ready({ children }: { children: (o: NonNullable<ReturnType<typeof useReady>>) => ReactNode }) {
  const ready = useReady();
  const { state, retry } = useDashboard();
  if (state.phase === "loading") {
    return (
      <div aria-busy="true" aria-label="Loading dashboard" className="flex flex-col gap-4">
        <div className="h-10 w-72 animate-pulse rounded-lg bg-border" />
        <div className="h-44 animate-pulse rounded-3xl bg-border/70" />
        <div className="grid gap-4 md:grid-cols-3">
          <div className="h-32 animate-pulse rounded-2xl bg-border/50" />
          <div className="h-32 animate-pulse rounded-2xl bg-border/50" />
          <div className="h-32 animate-pulse rounded-2xl bg-border/50" />
        </div>
      </div>
    );
  }
  if (state.phase === "error" || !ready) {
    const offline = state.phase === "error" && state.status === 0;
    return (
      <div role="alert" className="rounded-2xl border border-border bg-surface p-6">
        <p className="text-lg font-semibold text-ink">{offline ? "FarCare is offline" : "Couldn't load the dashboard"}</p>
        <p className="mt-1 text-body">
          {offline
            ? "We can't reach FarCare right now. Texts between the family and the Care Circle still work. Try again in a moment."
            : state.phase === "error"
              ? state.message
              : ""}
        </p>
        <button
          type="button"
          onClick={retry}
          className="mt-4 inline-flex min-h-11 items-center rounded-[10px] border border-border px-4 font-semibold text-ink hover:border-primary hover:text-primary"
        >
          Try again
        </button>
      </div>
    );
  }
  return <>{children(ready)}</>;
}

function useReady() {
  const { state } = useDashboard();
  return state.phase === "ready" ? state : null;
}

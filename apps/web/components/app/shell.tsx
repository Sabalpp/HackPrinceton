"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Activity,
  FlaskConical,
  HandHeart,
  HeartPulse,
  House,
  LayoutGrid,
  Lightbulb,
  ShieldAlert,
  Users,
  X,
  type LucideIcon,
} from "lucide-react";
import { API_URL, USE_MOCKS } from "@/lib/api";
import { useDashboard, type Connection } from "@/lib/dashboard";
import { isRequestOpen } from "@/lib/summary";
import { Logo } from "@/components/farcare/sidebar";
import { TimeAgo } from "@/components/ui/time";

const CONNECTION: Record<Connection, { label: string; dot: string }> = {
  live: { label: "Live", dot: "bg-primary animate-pulse" },
  connecting: { label: "Connecting", dot: "bg-sun" },
  reconnecting: { label: "Reconnecting", dot: "bg-sun" },
  offline: { label: "Offline", dot: "bg-muted" },
  mock: { label: "Demo", dot: "bg-sun" },
};

type Badge = { count: number; tone: "danger" | "primary" };
type NavItem = { href: string; label: string; short: string; Icon: LucideIcon; badge?: Badge };

const CONCEPT: [string, string][] = [
  ["/concept", "Daily call dashboard"],
  ["/call", "Live call"],
  ["/parent", "Parent phone"],
  ["/phone/digest", "Morning digest"],
  ["/setup", "Band setup"],
];

const DOCK = ["/", "/health", "/requests", "/safety"];

function useNav() {
  const { state } = useDashboard();
  const o = state.phase === "ready" ? state.overview : null;
  const alerts = o?.active_incidents.length ?? 0;
  const open = o?.recent_requests.filter(isRequestOpen).length ?? 0;
  const items: NavItem[] = [
    { href: "/", label: "Today", short: "Today", Icon: House },
    { href: "/health", label: "Health", short: "Health", Icon: HeartPulse },
    { href: "/requests", label: "Help requests", short: "Help", Icon: HandHeart, badge: open ? { count: open, tone: "primary" } : undefined },
    { href: "/safety", label: "Safety alerts", short: "Safety", Icon: ShieldAlert, badge: alerts ? { count: alerts, tone: "danger" } : undefined },
    { href: "/activity", label: "Activity", short: "Activity", Icon: Activity },
    { href: "/circle", label: "Care Circle", short: "Circle", Icon: Users },
  ];
  return { o, alerts, items };
}

function BadgeDot({ badge, floating = false }: { badge: Badge; floating?: boolean }) {
  return (
    <span
      className={`grid min-w-5 place-items-center rounded-full px-1.5 text-[0.7rem] font-bold leading-5 ${
        badge.tone === "danger" ? "bg-danger text-white" : "bg-primary text-on-primary"
      } ${floating ? "absolute -right-2.5 -top-1.5 ring-2 ring-side" : ""}`}
    >
      {badge.count}
      <span className="sr-only"> {badge.tone === "danger" ? "active alerts" : "open"}</span>
    </span>
  );
}

function ConnectionPill() {
  const { connection } = useDashboard();
  const c = CONNECTION[connection];
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap rounded-full bg-inset px-2.5 py-1 text-xs font-semibold text-body" role="status">
      <span className={`size-2 rounded-full ${c.dot}`} aria-hidden="true" />
      {c.label}
    </span>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { lastEvent } = useDashboard();
  const { o, alerts, items } = useNav();
  const [sheetPath, setSheetPath] = useState<string | null>(null);
  // The sheet belongs to the page it was opened on, so navigating closes it.
  const sheet = sheetPath === pathname;
  const active = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));
  const dock = items.filter((i) => DOCK.includes(i.href));
  const moreActive = !dock.some((i) => active(i.href));

  return (
    <div className="flex min-h-screen flex-1 bg-bg">
      <a
        href="#main"
        className="sr-only z-50 rounded-[10px] bg-primary px-4 py-3 font-bold text-on-primary focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
      >
        Skip to content
      </a>

      {/* ---------- Desktop sidebar ---------- */}
      <aside className="sticky top-0 hidden h-screen w-[264px] shrink-0 flex-col gap-7 overflow-y-auto border-r border-border bg-side p-6 md:flex">
        <div className="flex items-center justify-between gap-3">
          <Logo />
          <ConnectionPill />
        </div>
        <nav aria-label="Main" className="flex flex-col gap-2">
          <p className="px-3 text-[0.68rem] font-bold uppercase tracking-[0.16em] text-muted">Dashboard</p>
          <ul className="flex flex-col gap-1">
            {items.map(({ href, label, Icon, badge }) => (
              <li key={href}>
                <Link
                  href={href}
                  aria-current={active(href) ? "page" : undefined}
                  className={`relative flex min-h-11 items-center gap-3 rounded-[10px] px-3 font-medium transition-colors ${
                    active(href)
                      ? "bg-primary-tint text-ink before:absolute before:bottom-2 before:left-0 before:top-2 before:w-[3px] before:rounded-full before:bg-primary"
                      : "text-muted hover:bg-inset hover:text-ink"
                  }`}
                >
                  <Icon size={19} className={active(href) ? "text-primary" : ""} aria-hidden="true" />
                  <span className="flex-1 whitespace-nowrap">{label}</span>
                  {badge && <BadgeDot badge={badge} />}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <div className="mt-auto flex flex-col gap-3">
          {o && <ParentCard name={o.parent.display_name} timezone={o.parent.timezone} alert={alerts > 0} />}
          <Link
            href="/demo"
            aria-current={active("/demo") ? "page" : undefined}
            className={`flex min-h-11 items-center gap-2 rounded-[10px] border border-dashed px-3 text-sm font-semibold ${
              active("/demo") ? "border-primary bg-primary-tint text-primary" : "border-border text-muted hover:border-primary hover:text-primary"
            }`}
          >
            <FlaskConical size={16} aria-hidden="true" /> Demo controls
          </Link>
          <details className="text-sm text-muted">
            <summary className="cursor-pointer rounded px-1 py-2 hover:text-ink">Concept screens</summary>
            <ul className="mt-1 flex flex-col gap-1 pl-2">
              {CONCEPT.map(([href, label]) => (
                <li key={href}>
                  <Link href={href} className="inline-block py-1 hover:text-primary hover:underline">
                    {label}
                  </Link>
                </li>
              ))}
            </ul>
          </details>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* ---------- Phone top bar ---------- */}
        <header className="sticky top-0 z-30 border-b border-border bg-bg/90 pt-[env(safe-area-inset-top)] backdrop-blur-md md:hidden">
          <div className="flex h-14 items-center justify-between gap-3 px-4">
            <Logo />
            <div className="flex items-center gap-2">
              <ConnectionPill />
              {o && (
                <Link
                  href="/safety"
                  aria-label={alerts ? `${alerts} active safety alert` : `${o.parent.display_name}: no active alerts`}
                  className="relative grid size-11 place-items-center"
                >
                  <span className="grid size-9 place-items-center rounded-full bg-coral-tint font-bold text-coral-ink" aria-hidden="true">
                    {o.parent.display_name.charAt(0)}
                  </span>
                  <span className={`absolute bottom-1 right-1 size-3 rounded-full border-2 border-bg ${alerts ? "bg-danger" : "bg-primary"}`} aria-hidden="true" />
                </Link>
              )}
            </div>
          </div>
          {alerts > 0 && !active("/safety") && (
            <Link href="/safety" className="flex min-h-11 items-center justify-center gap-2 bg-danger px-4 text-sm font-bold text-white">
              <ShieldAlert size={16} aria-hidden="true" /> Safety alert in progress. Tap to open.
            </Link>
          )}
        </header>

        <main id="main" tabIndex={-1} className="mx-auto w-full max-w-[1180px] flex-1 px-4 pb-32 pt-5 outline-none sm:px-6 md:px-10 md:pb-12 md:pt-8">
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

      {/* ---------- Phone dock ---------- */}
      <nav aria-label="Main" className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-side/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-md md:hidden">
        <ul className="mx-auto grid max-w-lg grid-cols-5">
          {dock.map(({ href, short, Icon, badge }) => (
            <li key={href}>
              <Link href={href} aria-current={active(href) ? "page" : undefined} className="flex min-h-16 flex-col items-center justify-center gap-1">
                <DockIcon Icon={Icon} on={active(href)} badge={badge} />
                <span className={`text-[0.7rem] font-semibold ${active(href) ? "text-ink" : "text-muted"}`}>{short}</span>
              </Link>
            </li>
          ))}
          <li>
            <button
              type="button"
              onClick={() => setSheetPath(pathname)}
              aria-haspopup="dialog"
              aria-expanded={sheet}
              className="flex min-h-16 w-full flex-col items-center justify-center gap-1"
            >
              <DockIcon Icon={LayoutGrid} on={moreActive || sheet} />
              <span className={`text-[0.7rem] font-semibold ${moreActive || sheet ? "text-ink" : "text-muted"}`}>More</span>
            </button>
          </li>
        </ul>
      </nav>

      {sheet && <MoreSheet items={items.filter((i) => !DOCK.includes(i.href))} active={active} onClose={() => setSheetPath(null)} />}
    </div>
  );
}

function DockIcon({ Icon, on, badge }: { Icon: LucideIcon; on: boolean; badge?: Badge }) {
  return (
    <span className={`relative grid h-8 w-14 place-items-center rounded-full transition-colors ${on ? "bg-primary-tint text-primary" : "text-muted"}`}>
      <Icon size={21} strokeWidth={on ? 2.4 : 2} aria-hidden="true" />
      {badge && <BadgeDot badge={badge} floating />}
    </span>
  );
}

function ParentCard({ name, timezone, alert }: { name: string; timezone: string; alert: boolean }) {
  return (
    <div className="flex items-center gap-3 rounded-2xl border border-border bg-surface p-3">
      <span className="relative grid size-11 shrink-0 place-items-center rounded-full bg-coral-tint text-lg font-bold text-coral-ink" aria-hidden="true">
        {name.charAt(0)}
        <span className={`absolute -bottom-0.5 -right-0.5 size-3.5 rounded-full border-2 border-surface ${alert ? "bg-danger" : "bg-primary"}`} />
      </span>
      <div className="min-w-0">
        <p className="text-[0.68rem] font-bold uppercase tracking-[0.14em] text-muted">Caring for</p>
        <p className="truncate font-bold text-ink">{name}</p>
        <p className="truncate text-sm text-muted">{timezone.split("/").pop()?.replaceAll("_", " ")}</p>
      </div>
    </div>
  );
}

/** Bottom sheet on phones: the rest of the app, concept screens, and a testing panel. */
function MoreSheet({ items, active, onClose }: { items: NavItem[]; active: (h: string) => boolean; onClose: () => void }) {
  const { connection, lastEvent, state, timeline } = useDashboard();
  const first = useRef<HTMLAnchorElement>(null);

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    first.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      opener?.focus?.();
    };
  }, [onClose]);

  const viewer = state.phase === "ready" ? `${state.me.person.display_name} (${state.me.person.role})` : "–";
  const links: NavItem[] = [...items, { href: "/demo", label: "Demo controls", short: "Demo", Icon: FlaskConical }];

  return (
    <div className="fixed inset-0 z-50 md:hidden">
      <button type="button" aria-label="Close menu" tabIndex={-1} onClick={onClose} className="absolute inset-0 bg-black/60" />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="More"
        className="absolute inset-x-0 bottom-0 flex max-h-[85vh] flex-col gap-5 overflow-y-auto rounded-t-3xl border-t border-border bg-side px-5 pb-[calc(1.5rem+env(safe-area-inset-bottom))] pt-3"
      >
        <span className="mx-auto h-1.5 w-12 rounded-full bg-border" aria-hidden="true" />
        <div className="flex items-center justify-between">
          <p className="text-xs font-bold uppercase tracking-[0.16em] text-muted">More</p>
          <button type="button" onClick={onClose} aria-label="Close" className="grid size-11 place-items-center rounded-full text-muted hover:bg-inset hover:text-ink">
            <X size={20} aria-hidden="true" />
          </button>
        </div>

        <ul className="grid grid-cols-3 gap-2">
          {links.map(({ href, short, Icon }, i) => (
            <li key={href}>
              <Link
                ref={i === 0 ? first : undefined}
                href={href}
                aria-current={active(href) ? "page" : undefined}
                className={`flex min-h-20 flex-col items-center justify-center gap-2 rounded-2xl border text-sm font-semibold ${
                  active(href) ? "border-primary bg-primary-tint text-ink" : "border-border bg-surface text-body"
                }`}
              >
                <Icon size={22} className="text-primary" aria-hidden="true" />
                {short}
              </Link>
            </li>
          ))}
        </ul>

        <section aria-labelledby="concept-h" className="flex flex-col gap-2">
          <h2 id="concept-h" className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.16em] text-muted">
            <Lightbulb size={14} aria-hidden="true" /> Concept screens
          </h2>
          <ul className="flex flex-col divide-y divide-border rounded-2xl border border-border bg-surface">
            {CONCEPT.map(([href, label]) => (
              <li key={href}>
                <Link href={href} className="flex min-h-12 items-center px-4 text-body hover:text-ink">
                  {label}
                </Link>
              </li>
            ))}
          </ul>
        </section>

        <section aria-labelledby="test-h" className="flex flex-col gap-2">
          <h2 id="test-h" className="text-xs font-bold uppercase tracking-[0.16em] text-muted">
            Testing
          </h2>
          <dl className="grid grid-cols-1 gap-2 rounded-2xl border border-border bg-surface p-4 text-sm">
            {[
              ["Data source", USE_MOCKS ? "Demo data (in-browser mock)" : API_URL],
              ["Connection", CONNECTION[connection].label],
              ["Viewer", viewer],
              ["Events loaded", String(timeline.length)],
            ].map(([k, v]) => (
              <div key={k} className="flex justify-between gap-3">
                <dt className="text-muted">{k}</dt>
                <dd className="truncate text-right font-semibold text-ink">{v}</dd>
              </div>
            ))}
            <div className="flex justify-between gap-3">
              <dt className="text-muted">Last live event</dt>
              <dd className="text-right font-semibold text-ink">{lastEvent ? <TimeAgo iso={lastEvent.created_at} /> : "none yet"}</dd>
            </div>
          </dl>
        </section>
      </div>
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
        <div className="h-10 w-56 animate-pulse rounded-lg bg-inset" />
        <div className="h-64 animate-pulse rounded-3xl bg-inset" />
        <div className="grid gap-4 md:grid-cols-3">
          <div className="h-32 animate-pulse rounded-2xl bg-inset" />
          <div className="h-32 animate-pulse rounded-2xl bg-inset" />
          <div className="h-32 animate-pulse rounded-2xl bg-inset" />
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
            ? `We can't reach FarCare at ${API_URL}. Texts between the family and the Care Circle still work. Try again in a moment.`
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

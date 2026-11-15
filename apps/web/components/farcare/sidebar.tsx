"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { House, Activity, PhoneCall, Settings, Siren } from "lucide-react";
import { MOCK_STATE } from "@/lib/use-farcare-state";

export function Logo() {
  return (
    <Link href="/" className="flex min-h-11 items-center gap-2.5 rounded-[10px]">
      <span className="grid size-[34px] place-items-center rounded-[10px] bg-primary text-lg font-extrabold text-on-primary" aria-hidden="true">
        F
      </span>
      <span className="text-xl font-extrabold tracking-tight text-ink">
        Far<span className="text-primary">Care</span>
      </span>
    </Link>
  );
}

/** Left column on desktop, stacked on top on phones. */
export function SidebarFrame({ children }: { children: ReactNode }) {
  return (
    <aside className="flex w-full flex-col gap-7 border-b border-border bg-side p-5 md:sticky md:top-0 md:h-screen md:w-auto md:max-w-[280px] md:flex-[1_1_256px] md:overflow-y-auto md:border-b-0 md:border-r md:p-6">
      {children}
    </aside>
  );
}

const NAV = [
  { href: "/concept", label: "Today", Icon: House, match: (p: string) => p === "/concept" },
  { href: "/threads/1", label: "Health threads", Icon: Activity, match: (p: string) => p.startsWith("/threads") },
  { href: "/call", label: "Live call", Icon: PhoneCall, match: (p: string) => p.startsWith("/call") },
  { href: "/setup", label: "Setup", Icon: Settings, match: (p: string) => p.startsWith("/setup") },
];

export function Sidebar() {
  const pathname = usePathname();
  // Static identity for the sidebar; pages read live data through useFarCareState().
  const { parent } = MOCK_STATE;
  return (
    <SidebarFrame>
      <Logo />
      <nav aria-label="Main">
        <ul className="flex flex-wrap gap-1 md:flex-col">
          {NAV.map(({ href, label, Icon, match }) => {
            const active = match(pathname);
            return (
              <li key={href}>
                <Link
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={`flex min-h-11 items-center gap-3 rounded-[10px] px-3 ${
                    active ? "bg-primary-tint font-semibold text-primary" : "text-body hover:bg-inset"
                  }`}
                >
                  <Icon size={18} aria-hidden="true" />
                  {label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="mt-auto flex flex-col gap-3">
        <div className="rounded-2xl border border-border bg-inset p-4">
          <p className="text-sm text-muted">Caring for</p>
          <p className="font-semibold text-ink">
            {parent.nickname} · {parent.name}
          </p>
          <p className="text-sm text-muted">{parent.city}, Nepal</p>
        </div>
        <Link
          href="/emergency"
          className="flex min-h-11 items-center gap-2 rounded-[10px] border border-dashed border-border px-3 text-sm font-medium text-muted hover:border-danger hover:text-danger"
        >
          <Siren size={16} aria-hidden="true" />
          Demo: simulate emergency
        </Link>
      </div>
    </SidebarFrame>
  );
}

/** Shared two-column shell for the web pages. */
export function Shell({ sidebar, children }: { sidebar: ReactNode; children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-1 flex-wrap bg-bg">
      {sidebar}
      <main className="min-w-0 max-w-[1160px] flex-[999_1_560px] px-4 py-6 sm:px-8 md:px-10 md:py-8">
        {children}
        <footer className="mt-10 border-t border-border pt-5 text-sm text-muted">
          FarCare is a prototype, not a medical device. It doesn&apos;t diagnose anything and never contacts
          emergency services by itself. Demo people and band data are synthetic.
        </footer>
      </main>
    </div>
  );
}

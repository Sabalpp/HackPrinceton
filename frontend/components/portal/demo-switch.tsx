"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { HandHeart, Heart, Users } from "lucide-react";
import { roleHome, useSession } from "@/lib/session";

const VIEWS = [
  { role: "parent", label: "Parent", Icon: Heart },
  { role: "child", label: "Family", Icon: Users },
  { role: "responder", label: "Helper", Icon: HandHeart },
] as const;

/** Demo sessions only: jump between the sample family's three screens without signing out. */
export function DemoSwitch() {
  const { session, demo } = useSession();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const current = session?.person?.role;
  if (!session?.demo_session || !VIEWS.some(v => v.role === current)) return null;

  async function go(role: (typeof VIEWS)[number]["role"]) {
    if (role === current || busy) return;
    setBusy(true);
    try {
      const next = await demo(role);
      router.replace(next.person ? roleHome(next.person.role) : "/login");
    } finally {
      setBusy(false);
    }
  }

  return (
    <nav aria-label="Switch demo view" className="flex flex-wrap items-center gap-2 rounded-2xl border border-watch-border bg-watch-bg px-3 py-2 text-sm">
      <span className="font-semibold text-watch-heading">Demo · view as</span>
      <div className="flex gap-1">
        {VIEWS.map(({ role, label, Icon }) => {
          const on = role === current;
          return (
            <button
              key={role}
              type="button"
              aria-label={`View as ${label.toLowerCase()}`}
              aria-pressed={on}
              disabled={busy}
              onClick={() => void go(role)}
              className={`inline-flex min-h-9 items-center gap-1.5 rounded-full px-3 font-semibold disabled:opacity-60 ${on ? "bg-primary text-on-primary" : "bg-surface text-ink hover:text-primary"}`}
            >
              <Icon size={15} aria-hidden="true" />
              {label}
            </button>
          );
        })}
      </div>
    </nav>
  );
}

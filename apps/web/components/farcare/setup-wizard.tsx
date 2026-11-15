"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowLeft, ArrowRight, Check, CircleCheck, Link2 } from "lucide-react";
import { MOCK_STATE } from "@/lib/use-farcare-state";
import { Logo, Shell, SidebarFrame } from "./sidebar";
import { btn, ButtonLink, Card, CardTitle } from "./ui";

const STEPS = ["Your parent", "Daily call time", "Health basics", "Connect the band", "Emergency contacts", "Welcome call"];
const CURRENT = 3; // zero-based: "Connect the band"

function StepsSidebar() {
  return (
    <SidebarFrame>
      <Logo />
      <nav aria-label="Setup steps">
        <ol className="flex flex-col gap-1">
          {STEPS.map((label, i) => {
            const done = i < CURRENT;
            const current = i === CURRENT;
            return (
              <li
                key={label}
                aria-current={current ? "step" : undefined}
                className={`flex min-h-11 items-center gap-3 rounded-[10px] px-3 ${
                  current ? "bg-primary-tint font-semibold text-primary" : done ? "text-body" : "text-muted"
                }`}
              >
                <span
                  className={`grid size-7 shrink-0 place-items-center rounded-full text-sm font-bold ${
                    done ? "bg-primary text-on-primary" : current ? "border-2 border-primary text-primary" : "border border-border text-muted"
                  }`}
                  aria-hidden="true"
                >
                  {done ? <Check size={15} strokeWidth={3} /> : i + 1}
                </span>
                {label}
                <span className="sr-only">{done ? "(done)" : current ? "(current step)" : ""}</span>
              </li>
            );
          })}
        </ol>
      </nav>
      <Link href="/concept" className="mt-auto text-sm font-semibold text-primary underline-offset-4 hover:underline">
        Skip to dashboard
      </Link>
    </SidebarFrame>
  );
}

const CHECKS = [
  { id: "hub", title: "A spare Android phone stays at home as the sync hub", sub: "Plugged in near where she sleeps, on home Wi-Fi." },
  { id: "app", title: "Google Health app signed in to Ama's Google account", sub: "Use her account, not yours, so the data is hers." },
  { id: "band", title: "Fitbit Air paired and on her wrist", sub: "She only needs to wear it and charge it once a week." },
];

export function SetupWizard() {
  const nick = MOCK_STATE.parent.nickname;
  const [checked, setChecked] = useState<Record<string, boolean>>({ hub: true, app: true, band: true });
  const [connected, setConnected] = useState(true);
  const percent = Math.floor(((CURRENT + 1) / STEPS.length) * 100); // 66% at step 4 of 6

  return (
    <Shell sidebar={<StepsSidebar />}>
      <div className="flex max-w-3xl flex-col gap-6">
        <div className="flex flex-col gap-2">
          <p className="text-sm font-semibold text-muted">
            Step {CURRENT + 1} of {STEPS.length}
          </p>
          <div
            className="h-2 overflow-hidden rounded-full bg-border"
            role="progressbar"
            aria-valuenow={percent}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Setup progress"
          >
            <div className="h-full rounded-full bg-primary" style={{ width: `${percent}%` }} />
          </div>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-ink sm:text-4xl">Connect {nick}&apos;s Fitbit Air</h1>
          <p className="text-body">
            Do this once, during a visit home or with a relative on a video call. {nick} never has to touch the phone
            again.
          </p>
        </div>

        <Card>
          <CardTitle>Before you connect</CardTitle>
          <ul className="flex flex-col gap-4">
            {CHECKS.map((c) => (
              <li key={c.id}>
                <label className="flex cursor-pointer items-start gap-3">
                  <input
                    type="checkbox"
                    checked={checked[c.id] ?? false}
                    onChange={(e) => setChecked((v) => ({ ...v, [c.id]: e.target.checked }))}
                    className="mt-0.5 size-5 shrink-0 accent-primary"
                  />
                  <span>
                    <span className="block font-semibold text-ink">{c.title}</span>
                    <span className="block text-sm text-muted">{c.sub}</span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </Card>

        <Card>
          <CardTitle>Give FarCare read-only access</CardTitle>
          <p className="text-body">
            FarCare reads sleep, steps, heart rate and blood oxygen. It can&apos;t change anything on the band or in her
            account, and you can disconnect at any time.
          </p>
          <button
            type="button"
            className={`${btn.primary} mt-4`}
            onClick={() => setConnected(true)}
            disabled={!Object.values(checked).every(Boolean)}
          >
            <Link2 size={18} aria-hidden="true" />
            Connect Fitbit Air
          </button>
        </Card>

        {connected && (
          <p role="status" className="flex items-start gap-3 rounded-2xl border border-good-border bg-good-bg p-4 text-good">
            <CircleCheck size={22} className="mt-0.5 shrink-0" aria-hidden="true" />
            <span>
              <span className="font-semibold text-good-strong">Band connected</span> · last sync 3 min ago · learning{" "}
              {nick}&apos;s normal for the next 14 days
            </span>
          </p>
        )}

        <div className="flex flex-wrap justify-between gap-3">
          <button type="button" className={btn.outline}>
            <ArrowLeft size={18} aria-hidden="true" />
            Back
          </button>
          <ButtonLink href="/concept">
            Next: emergency contacts
            <ArrowRight size={18} aria-hidden="true" />
          </ButtonLink>
        </div>
      </div>
    </Shell>
  );
}

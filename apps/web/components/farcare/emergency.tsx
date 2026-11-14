"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowLeft, Check, Clock, Eye, Phone } from "lucide-react";
import type { FarCareState } from "@/lib/farcare-state";
import { StateGate } from "./state-gate";
import { btn, Card, CardTitle, Ne } from "./ui";

/*
 * DEMO incident (reached from "Demo: simulate emergency"). The real version will come from
 * state.events once the backend emits them. FarCare never contacts emergency services itself.
 */
const DEMO = {
  timeHer: "9:14 pm",
  saidEn: "Ama said she feels very dizzy",
  saidNe: "मलाई धेरै चक्कर लाग्यो",
  priyaSaw: "9:15 pm",
  escalateAfterMin: 10,
};

export function Emergency() {
  return <StateGate>{(s) => <EmergencyView state={s} />}</StateGate>;
}

function EmergencyView({ state: s }: { state: FarCareState }) {
  const [handling, setHandling] = useState(false);
  const [callNote, setCallNote] = useState<string | null>(null);
  const nick = s.parent.nickname;
  const helper = s.helpers.find((h) => h.confirmed) ?? s.helpers[0];
  const others = s.family.filter((f) => f.role !== "admin").map((f) => f.name);

  const steps: { done: boolean; text: string }[] = [
    { done: true, text: `Told ${nick} help is coming, and to sit down and stay on the line.` },
    ...(helper
      ? [{ done: true, text: `Called ${helper.name} (${helper.relationship}), who pressed 1 and is on the way.` }]
      : []),
    { done: true, text: `Alerted you${others.length ? ` and ${others.join(", ")}` : ""}.` },
    handling
      ? { done: true, text: "You're handling it. Everyone has been told." }
      : {
          done: false,
          text: `Waiting for someone to take charge. FarCare will call you if nobody acknowledges within ${DEMO.escalateAfterMin} min.`,
        },
  ];

  return (
    <div className="flex flex-col gap-6">
      <section role="alert" className="flex flex-col gap-4 rounded-2xl bg-danger p-6 text-white sm:p-8">
        <p className="text-sm font-bold tracking-wide text-danger-lighter">URGENT · {DEMO.timeHer} her time</p>
        <h1 className="text-3xl font-bold sm:text-4xl">{DEMO.saidEn}</h1>
        <blockquote className="text-danger-light">
          <Ne className="text-2xl">&ldquo;{DEMO.saidNe}&rdquo;</Ne>
          <span className="mt-1 block text-sm">&ldquo;I feel very dizzy&rdquo;</span>
        </blockquote>
        <button
          type="button"
          onClick={() => setHandling(true)}
          disabled={handling}
          className="inline-flex min-h-14 items-center justify-center gap-2 self-start rounded-[10px] bg-white px-6 text-lg font-bold text-danger hover:bg-danger-light disabled:opacity-90"
        >
          {handling && <Check size={22} aria-hidden="true" />}
          {handling ? "You're handling it" : "I'm handling it"}
        </button>
      </section>

      <div className="flex flex-wrap items-start gap-6">
        <Card className="flex-[2_1_420px]">
          <CardTitle>What FarCare already did</CardTitle>
          <ol className="flex flex-col gap-3">
            {steps.map((st) => (
              <li key={st.text} className="flex items-start gap-3">
                <span
                  className={`mt-0.5 grid size-6 shrink-0 place-items-center rounded-full ${
                    st.done ? "bg-good-bg text-good" : "bg-watch-bg text-watch-label"
                  }`}
                  aria-hidden="true"
                >
                  {st.done ? <Check size={15} strokeWidth={3} /> : <Clock size={15} strokeWidth={2.5} />}
                </span>
                <span className={st.done ? "text-body" : "font-semibold text-ink"}>
                  {st.text}
                  <span className="sr-only">{st.done ? " (done)" : " (waiting)"}</span>
                </span>
              </li>
            ))}
          </ol>
          <p className="mt-5 rounded-xl bg-inset p-4 text-body">
            If you think {nick} is in danger, call local emergency services
            {s.parent.emergency_number ? ` (${s.parent.emergency_number} in Nepal)` : ""}. FarCare never calls them for
            you.
          </p>
        </Card>

        <div className="flex flex-[1_1_280px] flex-col gap-6">
          <Card>
            <div className="flex flex-col gap-2">
              <button type="button" className={btn.primary} onClick={() => setCallNote(nick)}>
                <Phone size={18} aria-hidden="true" />
                Call {nick} now
              </button>
              {helper && (
                <button type="button" className={btn.outline} onClick={() => setCallNote(helper.name)}>
                  <Phone size={18} aria-hidden="true" />
                  Call {helper.name}
                </button>
              )}
              {callNote && (
                <p role="status" className="text-sm text-muted">
                  Demo: phone numbers for {callNote} aren&apos;t stored in this prototype.
                </p>
              )}
            </div>
          </Card>
          <Card>
            <CardTitle>Family</CardTitle>
            <ul className="flex flex-col gap-2">
              {others.map((name) => (
                <li key={name} className="flex items-center gap-2 text-body">
                  <Eye size={16} className="text-primary" aria-hidden="true" />
                  {name} saw this alert · {DEMO.priyaSaw}
                </li>
              ))}
            </ul>
          </Card>
          <Link href="/concept" className={`${btn.quiet} self-start px-2`}>
            <ArrowLeft size={18} aria-hidden="true" />
            Back to Today
          </Link>
        </div>
      </div>
    </div>
  );
}

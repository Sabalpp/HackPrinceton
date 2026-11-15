"use client";

import { useState, type FormEvent } from "react";
import { Check, MessageCirclePlus, PhoneOutgoing, Smartphone } from "lucide-react";
import type { FarCareState, Thread } from "@/lib/farcare-state";
import { hourIn, longDate } from "@/lib/time";
import { useNow } from "@/lib/use-now";
import { StateGate } from "./state-gate";
import { calledLabel, FamilyChip, MedDots, StatusCard, ThreadCard, VitalTile } from "./pieces";
import { btn, ButtonLink, Card, CardTitle, SimulatedPill } from "./ui";

export function Today() {
  return <StateGate>{(s) => <TodayView state={s} />}</StateGate>;
}

function Greeting({ name, tz }: { name: string; tz: string }) {
  const now = useNow(60_000);
  const h = now === null ? 9 : hourIn(now, tz);
  const part = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  return (
    <h1 className="text-3xl font-bold tracking-tight text-ink sm:text-4xl">
      {part}, {name}
    </h1>
  );
}

function TodayView({ state: s }: { state: FarCareState }) {
  const viewer = s.family.find((f) => f.role === "admin") ?? s.family[0];
  const nick = s.parent.nickname;
  const [called, setCalled] = useState(false);
  const [asking, setAsking] = useState(false);
  const [extraThreads, setExtraThreads] = useState<Thread[]>([]);
  const threads = [...s.threads, ...extraThreads];

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="font-medium text-muted">{longDate(s.today)}</p>
          <Greeting name={viewer.name} tz={viewer.tz} />
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={btn.outline} onClick={() => setCalled(true)} disabled={called}>
            {called ? <Check size={18} aria-hidden="true" /> : <PhoneOutgoing size={18} aria-hidden="true" />}
            {called ? `Logged: you called ${nick}` : `I called ${nick}`}
          </button>
          <button type="button" className={btn.primary} onClick={() => setAsking((v) => !v)} aria-expanded={asking}>
            <MessageCirclePlus size={18} aria-hidden="true" />
            Ask {nick} something
          </button>
        </div>
      </header>

      {asking && (
        <AskForm
          nick={nick}
          onAsk={(q) => {
            setExtraThreads((t) => [
              ...t,
              { id: 1000 + t.length, topic: q, kind: "child_question", created_by: viewer.name, history: [] },
            ]);
            setAsking(false);
          }}
          onCancel={() => setAsking(false)}
        />
      )}

      {/* Family across time zones */}
      <ul className="flex flex-wrap gap-2" aria-label="Family local times">
        <FamilyChip label={nick} city={s.parent.city} tz={s.parent.tz} dot="sun" />
        {s.family.map((m) => (
          <FamilyChip
            key={m.name}
            label={m === viewer ? "You" : m.name}
            city={m.city}
            tz={m.tz}
            dot="green"
            extra={m === viewer ? undefined : calledLabel(m, nick, s.today)}
          />
        ))}
      </ul>

      <StatusCard
        level={s.status}
        headline={s.status_headline}
        sub={`From last night's check-in call and ${nick}'s band data.`}
        parent={s.parent}
        family={s.family}
        viewer={viewer}
      />

      {/* Threads + digest */}
      <div className="flex flex-wrap gap-6">
        <Card className="flex-[2_1_420px]">
          <CardTitle aside={`${threads.length} open`}>Health threads</CardTitle>
          <ul className="flex flex-col gap-3">
            {threads.map((t) => (
              <ThreadCard key={t.id} thread={t} today={s.today} />
            ))}
          </ul>
        </Card>

        <Card className="flex flex-[1_1_300px] flex-col">
          <CardTitle aside="Sent 7:30 am · iMessage">Today&apos;s digest</CardTitle>
          <p className="whitespace-pre-line rounded-xl bg-primary-tint p-4 leading-relaxed text-on-tint">{s.digest}</p>
          <ButtonLink href="/phone/digest" variant="quiet" className="mt-3 self-start px-2">
            <Smartphone size={18} aria-hidden="true" />
            See it on your phone
          </ButtonLink>
        </Card>
      </div>

      {/* Vitals */}
      <Card>
        <CardTitle
          aside={
            <span className="inline-flex flex-wrap items-center gap-2">
              {s.band ? `${s.band.name} · last sync ${s.band.synced_min_ago} min ago` : "No band connected"}
              <SimulatedPill />
            </span>
          }
        >
          Vitals vs her normal
        </CardTitle>
        <ul className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-3">
          {s.vitals.map((v) => (
            <VitalTile key={v.metric} vital={v} />
          ))}
        </ul>
        <div className="mt-5 border-t border-border pt-5">
          <MedDots taken={s.meds_last7} today={s.today} />
        </div>
        <p className="mt-4 text-sm text-muted">
          Compared with {nick}&apos;s own last 14 days. Not a diagnosis.
        </p>
      </Card>
    </div>
  );
}

function AskForm({ nick, onAsk, onCancel }: { nick: string; onAsk: (q: string) => void; onCancel: () => void }) {
  const [text, setText] = useState("");
  function submit(e: FormEvent) {
    e.preventDefault();
    if (text.trim()) onAsk(text.trim());
  }
  return (
    <Card as="div">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label htmlFor="ask-ama" className="font-semibold text-ink">
          What should FarCare ask {nick} on tonight&apos;s call?
        </label>
        <input
          id="ask-ama"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Did you go to Dr. Sharma this week?"
          className="min-h-11 rounded-[10px] border border-border bg-surface px-3 text-ink placeholder:text-muted"
        />
        <div className="flex flex-wrap gap-2">
          <button type="submit" className={btn.primary} disabled={!text.trim()}>
            Add to tonight&apos;s call
          </button>
          <button type="button" className={btn.outline} onClick={onCancel}>
            Cancel
          </button>
        </div>
      </form>
    </Card>
  );
}

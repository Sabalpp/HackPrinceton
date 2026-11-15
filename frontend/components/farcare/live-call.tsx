"use client";

import { useState, type FormEvent } from "react";
import { PhoneOff, Send, ShieldCheck } from "lucide-react";
import type { FarCareState, TranscriptLine } from "@/lib/farcare-state";
import { clock } from "@/lib/time";
import { farcareApi } from "@/lib/farcare-api";
import { useNow } from "@/lib/use-now";
import { StateGate } from "./state-gate";
import { AgendaList, ChatBubble } from "./pieces";
import { btn, Card, CardTitle, StatusPill } from "./ui";

// Demo: the call "started" 1:42 before this page loaded. Module scope, so it's not read during render.
const CALL_STARTED_AT = typeof window === "undefined" ? 0 : Date.now() - 102_000;

export function LiveCall() {
  return <StateGate>{(s) => <LiveCallView state={s} />}</StateGate>;
}

function Elapsed({ endedAt }: { endedAt: number | null }) {
  const now = useNow(1000);
  if (now === null) return <span className="tabular">00:00</span>;
  const sec = Math.max(0, Math.floor(((endedAt ?? now) - CALL_STARTED_AT) / 1000));
  return (
    <span className="tabular" aria-label={`${Math.floor(sec / 60)} minutes ${sec % 60} seconds`}>
      {String(Math.floor(sec / 60)).padStart(2, "0")}:{String(sec % 60).padStart(2, "0")}
    </span>
  );
}

function LiveCallView({ state: s }: { state: FarCareState }) {
  const nick = s.parent.nickname;
  const [lines, setLines] = useState<TranscriptLine[]>(s.latest_call.transcript);
  const [endedAt, setEndedAt] = useState<number | null>(null);
  const [h, m] = s.parent.call_time.split(":").map(Number);
  const callLabel = clock(Date.UTC(2000, 0, 1, h, m), "UTC");

  return (
    <div className="flex flex-col gap-6">
      {/* Status bar */}
      <div
        className={`flex flex-wrap items-center justify-between gap-3 rounded-2xl px-5 py-4 ${
          endedAt ? "border border-border bg-surface text-ink" : "bg-primary text-on-primary"
        }`}
      >
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span
            className={`size-3 rounded-full ${endedAt ? "bg-muted" : "animate-pulse bg-live"}`}
            aria-hidden="true"
          />
          <span className="font-bold">{endedAt ? `Call with ${nick} ended` : `On a call with ${nick}`}</span>
          <span className={endedAt ? "text-muted" : "text-on-primary/80"}>
            · Tonight&apos;s check-in · {callLabel} her time · <Elapsed endedAt={endedAt} />
          </span>
        </p>
        {!endedAt && (
          <button
            type="button"
            onClick={() => setEndedAt(Date.now())}
            className="inline-flex min-h-11 items-center gap-2 rounded-[10px] border border-on-primary/40 px-4 font-semibold text-on-primary hover:bg-on-primary hover:text-primary"
          >
            <PhoneOff size={18} aria-hidden="true" />
            End call
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-start gap-6">
        <Card className="flex flex-[2_1_420px] flex-col">
          <CardTitle aside="Nepali, with English translation">Transcript</CardTitle>
          <ul className="flex flex-col gap-3" aria-live="polite">
            {lines.map((l, i) => (
              <ChatBubble key={i} line={l} nickname={nick} />
            ))}
          </ul>
          {!endedAt && (
            <p className="mt-4 flex items-center gap-2 text-muted" role="status">
              <span className="flex gap-1" aria-hidden="true">
                {[0, 150, 300].map((d) => (
                  <span key={d} className="size-1.5 animate-bounce rounded-full bg-primary" style={{ animationDelay: `${d}ms` }} />
                ))}
              </span>
              Listening…
            </p>
          )}
          <AnswerForm
            nick={nick}
            disabled={endedAt !== null}
            onSent={(text) => setLines((ls) => [...ls, { role: "ama", ne: "", en: `${text} (typed)` }])}
          />
        </Card>

        <div className="flex flex-[1_1_280px] flex-col gap-6">
          <Card>
            <CardTitle>Tonight&apos;s agenda</CardTitle>
            <AgendaList items={s.agenda} />
          </Card>
          <Card>
            <CardTitle>Learned so far</CardTitle>
            <ul className="flex flex-wrap gap-2">
              {(s.latest_call.learned ?? []).map((l) => (
                <li key={l.label}>
                  <StatusPill tone="tint">
                    {l.label}: {l.value}
                  </StatusPill>
                </li>
              ))}
            </ul>
            <p className="mt-4 flex items-center gap-2 text-sm text-muted">
              <ShieldCheck size={16} className="text-primary" aria-hidden="true" />
              Safety check runs on every answer.
            </p>
          </Card>
        </div>
      </div>
    </div>
  );
}

function AnswerForm({ nick, disabled, onSent }: { nick: string; disabled: boolean; onSent: (t: string) => void }) {
  const [text, setText] = useState("");
  const [status, setStatus] = useState<"idle" | "sending" | "error">("idle");

  async function submit(e: FormEvent) {
    e.preventDefault();
    const value = text.trim();
    if (!value) return;
    setStatus("sending");
    try {
      await farcareApi.callAnswer(value);
      onSent(value);
      setText("");
      setStatus("idle");
    } catch {
      setStatus("error");
    }
  }

  return (
    <form onSubmit={submit} className="mt-5 flex flex-col gap-2 border-t border-border pt-5">
      <label htmlFor="typed-answer" className="text-sm font-semibold text-ink">
        Type {nick}&apos;s answer (backup if voice fails)
      </label>
      <div className="flex flex-wrap gap-2">
        <input
          id="typed-answer"
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={disabled}
          className="min-h-11 min-w-0 flex-[1_1_200px] rounded-[10px] border border-border bg-surface px-3 text-ink"
        />
        <button type="submit" className={btn.primary} disabled={disabled || !text.trim() || status === "sending"}>
          <Send size={18} aria-hidden="true" />
          {status === "sending" ? "Sending…" : "Send"}
        </button>
      </div>
      {status === "error" && (
        <p role="alert" className="text-sm font-medium text-danger">
          Couldn&apos;t send. Check the connection and try again.
        </p>
      )}
    </form>
  );
}

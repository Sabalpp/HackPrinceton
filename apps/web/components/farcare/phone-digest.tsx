"use client";

import { useState, type FormEvent } from "react";
import { ArrowUp } from "lucide-react";
import { MOCK_STATE, useFarCareState } from "@/lib/use-farcare-state";
import { PhoneFrame } from "./phone-frame";

type Msg = { from: "farcare" | "me"; text: string };

export function PhoneDigest() {
  const result = useFarCareState();
  const s = result.phase === "ready" ? result.data : MOCK_STATE;
  const nick = s.parent.nickname;
  const personal = s.threads.find((t) => t.kind === "personal");
  const [msgs, setMsgs] = useState<Msg[]>([
    { from: "me", text: "Ask her if she went to Dr. Sharma" },
    { from: "farcare", text: `Got it. I'll ask ${nick} tonight and tell you and Priya tomorrow morning.` },
  ]);
  const [draft, setDraft] = useState("");

  function send(e: FormEvent) {
    e.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setMsgs((m) => [...m, { from: "me", text }, { from: "farcare", text: `Got it. I'll ask ${nick} tonight.` }]);
    setDraft("");
  }

  return (
    <PhoneFrame label="Your phone: FarCare morning update">
      <header className="flex flex-col items-center gap-1 border-b border-border bg-bg px-4 pb-3 pt-6">
        <span className="grid size-11 place-items-center rounded-full bg-primary text-lg font-bold text-white" aria-hidden="true">
          F
        </span>
        <p className="font-semibold text-ink">FarCare</p>
        <p className="text-xs text-muted">Today 7:30 AM</p>
      </header>

      <ul className="flex flex-1 flex-col gap-2 overflow-y-auto px-3 py-4" aria-live="polite">
        <Bubble from="farcare">
          <span className="whitespace-pre-line">{s.digest}</span>
        </Bubble>
        {personal && (
          <Bubble from="farcare">
            <span className="font-semibold">Talk about…</span> {personal.topic}. A happy thing to ask {nick} about when you call.
          </Bubble>
        )}
        {msgs.map((m, i) => (
          <Bubble key={i} from={m.from}>
            {m.text}
          </Bubble>
        ))}
      </ul>

      <form onSubmit={send} className="flex items-center gap-2 border-t border-border p-3">
        <label htmlFor="phone-msg" className="sr-only">
          Message FarCare
        </label>
        <input
          id="phone-msg"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Message"
          className="min-h-11 min-w-0 flex-1 rounded-full border border-border bg-surface px-4 text-ink placeholder:text-muted"
        />
        <button
          type="submit"
          aria-label="Send"
          disabled={!draft.trim()}
          className="grid size-11 shrink-0 place-items-center rounded-full bg-primary text-white hover:bg-primary-hover disabled:opacity-50"
        >
          <ArrowUp size={20} aria-hidden="true" />
        </button>
      </form>
    </PhoneFrame>
  );
}

function Bubble({ from, children }: { from: "farcare" | "me"; children: React.ReactNode }) {
  const me = from === "me";
  return (
    <li className={`flex ${me ? "justify-end" : "justify-start"}`}>
      <p
        className={`max-w-[80%] rounded-2xl px-3.5 py-2 text-[15px] leading-snug ${
          me ? "rounded-br-md bg-primary text-white" : "rounded-bl-md bg-neutral-bg text-ink"
        }`}
      >
        {children}
      </p>
    </li>
  );
}

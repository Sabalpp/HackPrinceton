"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { MessageCircle, Send, Smartphone } from "lucide-react";
import { ApiError } from "@/lib/api";
import { experience, type ChatMessage } from "@/lib/experience-api";
import { usePortalData } from "@/lib/portal";
import { PortalCard, inputClass } from "./ui";

/**
 * The signed-in person's conversation with FarCare. It is the SAME conversation as their
 * iMessage/SMS thread: same assistant, same memory, same actions. A message typed here is
 * answered here; texts sent from a phone appear here too.
 */

const CHANNEL_LABEL: Record<string, string> = {
  imessage: "iMessage",
  sms: "SMS",
  voice: "Voice call",
  app: "App",
  in_app: "App",
  photon: "iMessage",
  console: "Simulated",
  simulated: "Simulated",
};

const SUGGESTIONS: Record<"parent" | "responder" | "child", string[]> = {
  parent: ["Can someone help me with groceries tomorrow?", "Any updates?", "I'm feeling a bit tired today"],
  responder: ["What do I need to do?", "Yes, I can help in 15 minutes", "Done"],
  child: ["How is Mom doing?", "Can someone check on Mom today?"],
};

function time(iso: string) {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function Bubble({ message }: { message: ChatMessage }) {
  const mine = message.direction === "in";
  return (
    <li className={`flex flex-col gap-1 ${mine ? "items-end" : "items-start"}`}>
      <p className={`max-w-[85%] whitespace-pre-wrap break-words rounded-2xl px-4 py-3 text-base leading-relaxed sm:text-lg ${mine ? "rounded-br-md bg-primary text-on-primary" : "rounded-bl-md border border-border bg-inset text-ink"}`}>
        {message.text}
      </p>
      <span className="px-1 text-xs text-muted">
        {mine ? "You" : "FarCare"} · {time(message.at)} · {CHANNEL_LABEL[message.channel] ?? message.channel}
      </span>
    </li>
  );
}

export function FarCareChat({ role, onActivity }: { role: "parent" | "responder" | "child"; onActivity?: () => void }) {
  const result = usePortalData(experience.conversation);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const list = useRef<HTMLUListElement>(null);
  const messages = result.data?.messages ?? [];

  useEffect(() => {
    list.current?.scrollTo({ top: list.current.scrollHeight });
  }, [messages.length]);

  async function send(value: string) {
    const body = value.trim();
    if (!body || sending) return;
    setSending(true);
    setError(null);
    try {
      await experience.sendMessage(body);
      setText("");
      await result.refresh();
      onActivity?.(); // a message can create or change a request: refresh the screen around us
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Your message didn't send. Try again.");
    } finally {
      setSending(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void send(text);
  }

  return (
    <PortalCard
      title="Chat with FarCare"
      description="The same assistant you can text. Ask for help, check on things, or reply to a request."
    >
      <div className="flex flex-col gap-4">
        {result.data && (
          <p className="flex items-center gap-2 text-sm text-muted">
            <Smartphone size={17} aria-hidden="true" />
            {result.data.connected_by_text
              ? "Your phone is connected: texts and this chat are one conversation."
              : result.data.messaging === "photon"
                ? "Text your FarCare number once to continue this chat from your phone."
                : "Phone messages are simulated on this server; this chat is live."}
          </p>
        )}
        {result.loading && <p role="status" className="text-muted">Loading your conversation…</p>}
        {result.error && <p role="alert" className="text-danger">{result.error}</p>}
        {!result.loading && messages.length === 0 && (
          <div className="flex items-center gap-3 rounded-2xl border border-dashed border-border px-4 py-6 text-body">
            <MessageCircle size={24} className="shrink-0 text-primary" aria-hidden="true" />
            Say hello, or try one of the suggestions below.
          </div>
        )}
        {messages.length > 0 && (
          <ul ref={list} aria-label="Conversation with FarCare" aria-live="polite" className="flex max-h-[28rem] flex-col gap-4 overflow-y-auto pr-1">
            {messages.map(message => <Bubble key={message.id} message={message} />)}
          </ul>
        )}
        <div className="flex flex-wrap gap-2" aria-label="Suggestions">
          {SUGGESTIONS[role].map(s => (
            <button key={s} type="button" disabled={sending} onClick={() => void send(s)} className="min-h-11 rounded-full border border-border bg-surface px-4 text-sm font-semibold text-ink hover:border-primary disabled:opacity-60">
              {s}
            </button>
          ))}
        </div>
        <form onSubmit={submit} className="flex gap-2">
          <label htmlFor={`chat-${role}`} className="sr-only">Message FarCare</label>
          <input id={`chat-${role}`} value={text} onChange={e => setText(e.target.value)} maxLength={1000} placeholder="Type a message…" autoComplete="off" className={inputClass} />
          <button type="submit" disabled={sending || !text.trim()} aria-label="Send message" className="inline-flex min-h-13 min-w-13 items-center justify-center rounded-xl border border-primary bg-primary px-4 text-on-primary hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50">
            <Send size={21} aria-hidden="true" />
          </button>
        </form>
        {sending && <p role="status" className="text-sm text-muted">FarCare is replying…</p>}
        {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      </div>
    </PortalCard>
  );
}

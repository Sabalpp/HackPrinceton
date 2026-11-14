"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowLeft, CalendarClock, CircleCheck, MessageCircleQuestion, Users } from "lucide-react";
import type { FarCareState, Thread } from "@/lib/farcare-state";
import { shortDate, weekdayOf } from "@/lib/time";
import { StateGate } from "./state-gate";
import { sourceIcon, threadPill, Timeline, type TimelineItem } from "./pieces";
import { btn, Card, CardTitle, Ne, StatusPill, type PillTone } from "./ui";

const ENTRY_PILL: Record<string, { tone: PillTone; label: string }> = {
  new: { tone: "watch", label: "New" },
  improving: { tone: "good", label: "Improving" },
  same: { tone: "neutral", label: "No change" },
  worse: { tone: "watch", label: "Worse" },
  fine: { tone: "good", label: "Fine" },
};

export function ThreadView({ id }: { id: number }) {
  return <StateGate>{(s) => <ThreadBody state={s} id={id} />}</StateGate>;
}

function ThreadBody({ state: s, id }: { state: FarCareState; id: number }) {
  const thread = s.threads.find((t) => t.id === id);
  if (!thread) {
    return (
      <div className="flex flex-col gap-4">
        <BackLink />
        <Card>
          <p className="font-semibold text-ink">This thread doesn&apos;t exist anymore.</p>
          <p className="text-body">It may have been resolved and archived.</p>
        </Card>
      </div>
    );
  }
  return <ThreadDetail thread={thread} state={s} />;
}

function BackLink() {
  return (
    <Link href="/concept" className={`${btn.quiet} self-start px-2`}>
      <ArrowLeft size={18} aria-hidden="true" />
      Back to Today
    </Link>
  );
}

function buildItems(t: Thread, nick: string): TimelineItem[] {
  const items: TimelineItem[] = t.history.map((h, i) => ({
    key: `${h.day}-${i}`,
    date: shortDate(h.day),
    source: h.source === "band" ? "Band" : h.source === "child" ? "Family" : "Daily call",
    sourceIcon: sourceIcon(h.source),
    pill: h.status ? ENTRY_PILL[h.status] : undefined,
    body: (
      <div className="flex flex-col gap-2">
        {h.ask_en && (
          <p className="text-muted">
            FarCare asked: &ldquo;{h.ask_en}&rdquo;
            {h.ask_ne && (
              <>
                {" "}
                <Ne>({h.ask_ne})</Ne>
              </>
            )}
          </p>
        )}
        <p className="text-ink">
          {h.source === "call" && h.ask_en ? <>{nick}: &ldquo;{h.note}&rdquo;</> : h.note}
        </p>
        {h.quote_ne && (
          <blockquote className="rounded-xl bg-inset px-4 py-2">
            <Ne className="text-lg text-ink">&ldquo;{h.quote_ne}&rdquo;</Ne>
          </blockquote>
        )}
      </div>
    ),
  }));
  if (t.next_followup && t.status !== "resolved") {
    items.push({
      key: "scheduled",
      date: shortDate(t.next_followup),
      source: "Scheduled",
      sourceIcon: CalendarClock,
      scheduled: true,
      body: <p className="text-muted">FarCare will ask again on {weekdayOf(t.next_followup)}&apos;s call.</p>,
    });
  }
  return items;
}

function ThreadDetail({ thread: t, state: s }: { thread: Thread; state: FarCareState }) {
  const [resolved, setResolved] = useState(t.status === "resolved");
  const [asking, setAsking] = useState(false);
  const [question, setQuestion] = useState("");
  const [queued, setQueued] = useState<string[]>([]);
  const pill = resolved ? { tone: "neutral" as const, label: "Resolved" } : threadPill(t, s.today);
  const shortPill = { ...pill, label: pill.label.split(" · ")[0] };
  const followUps = Math.max(0, t.history.length - 1);
  const items = buildItems(t, s.parent.nickname);
  const sharedWith = ["You", ...s.family.filter((f) => f.role !== "admin").map((f) => f.name)];

  return (
    <div className="flex flex-col gap-6">
      <BackLink />

      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-2">
          <div>
            <StatusPill tone={shortPill.tone}>{shortPill.label}</StatusPill>
          </div>
          <h1 className="text-3xl font-bold tracking-tight text-ink sm:text-4xl">{t.topic}</h1>
          <p className="text-muted">
            {t.first_seen && `First mentioned ${shortDate(t.first_seen)}`}
            {t.first_seen && ` · ${followUps} follow-up${followUps === 1 ? "" : "s"}`}
            {t.next_followup && !resolved && ` · next follow-up ${weekdayOf(t.next_followup)}, ${shortDate(t.next_followup)}`}
            {!t.first_seen && "Not discussed on a call yet"}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={btn.outline} onClick={() => setResolved(true)} disabled={resolved}>
            <CircleCheck size={18} aria-hidden="true" />
            {resolved ? "Resolved" : "Mark resolved"}
          </button>
          <button type="button" className={btn.primary} onClick={() => setAsking((v) => !v)} aria-expanded={asking}>
            <MessageCircleQuestion size={18} aria-hidden="true" />
            Ask a specific question
          </button>
        </div>
      </header>

      {asking && (
        <Card as="div">
          <form
            className="flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (!question.trim()) return;
              setQueued((q) => [...q, question.trim()]);
              setQuestion("");
              setAsking(false);
            }}
          >
            <label htmlFor="thread-question" className="font-semibold text-ink">
              What should FarCare ask {s.parent.nickname} about {t.topic.toLowerCase()}?
            </label>
            <input
              id="thread-question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Can you climb the stairs without pain?"
              className="min-h-11 rounded-[10px] border border-border bg-surface px-3 text-ink placeholder:text-muted"
            />
            <div className="flex flex-wrap gap-2">
              <button type="submit" className={btn.primary} disabled={!question.trim()}>
                Add to the next call
              </button>
              <button type="button" className={btn.outline} onClick={() => setAsking(false)}>
                Cancel
              </button>
            </div>
          </form>
        </Card>
      )}
      {queued.length > 0 && (
        <p role="status" className="rounded-2xl border border-good-border bg-good-bg p-4 text-good">
          Added to the next call: {queued.map((q) => `“${q}”`).join(", ")}
        </p>
      )}

      <div className="flex flex-wrap items-start gap-6">
        <Card className="flex-[2_1_420px]">
          <CardTitle>What FarCare remembers</CardTitle>
          {items.length > 0 ? (
            <Timeline items={items} />
          ) : (
            <p className="text-body">Nothing yet. FarCare will bring this up on the next call.</p>
          )}
        </Card>

        <div className="flex flex-[1_1_280px] flex-col gap-6">
          <Card>
            <CardTitle>How FarCare follows up</CardTitle>
            <dl className="flex flex-col gap-3">
              {[
                ["New or worse", "asks again tomorrow"],
                ["Improving", "asks again in 2 days"],
                ["Fine twice", "marked resolved"],
                ["Worse, or 5+ days", "suggests the family arrange a doctor visit"],
              ].map(([k, v]) => (
                <div key={k} className="flex flex-col">
                  <dt className="font-semibold text-ink">{k}</dt>
                  <dd className="text-body">{v}</dd>
                </div>
              ))}
            </dl>
          </Card>
          <Card>
            <p className="flex items-start gap-3 text-body">
              <Users size={20} className="mt-0.5 shrink-0 text-primary" aria-hidden="true" />
              <span>
                <span className="font-semibold text-ink">Shared with:</span> {sharedWith.join(" and ")}.{" "}
                {s.parent.nickname} agreed on Nov 10.
              </span>
            </p>
          </Card>
        </div>
      </div>
    </div>
  );
}

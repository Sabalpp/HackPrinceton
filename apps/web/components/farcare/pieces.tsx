"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import {
  Check,
  ChevronRight,
  Droplet,
  Footprints,
  Gauge,
  HeartPulse,
  Moon,
  Phone,
  Watch,
  type LucideIcon,
} from "lucide-react";
import type { AgendaItem, FamilyMember, Level, Parent, Thread, TranscriptLine, Vital } from "@/lib/farcare-state";
import { addDays, clock, daysBetween, hoursMinutes, nextCall, shortDate, weekdayOf } from "@/lib/time";
import { useNow } from "@/lib/use-now";
import { Ne, StatusPill, type PillTone } from "./ui";

/* ---------- FamilyChip ---------- */

export function FamilyChip({
  label,
  city,
  tz,
  dot,
  extra,
}: {
  label: string;
  city: string;
  tz: string;
  dot: "sun" | "green";
  extra?: string;
}) {
  const now = useNow(10_000);
  return (
    <li className="inline-flex min-h-9 items-center gap-2 rounded-full border border-border bg-surface px-3.5 py-1.5 text-sm text-body">
      <span className={`size-2.5 rounded-full ${dot === "sun" ? "bg-sun" : "bg-primary"}`} aria-hidden="true" />
      <span>
        <span className="font-semibold text-ink">{label}</span> · {city}
        {now !== null && <span className="tabular"> · {clock(now, tz)}</span>}
        {extra && ` · ${extra}`}
      </span>
    </li>
  );
}

export function calledLabel(m: FamilyMember, parentNick: string, today: string): string | undefined {
  if (!m.last_called) return undefined;
  const ago = daysBetween(m.last_called, today);
  if (ago === 0) return `called ${parentNick} today`;
  if (ago < 7) return `called ${parentNick} ${weekdayOf(m.last_called)}`;
  return undefined;
}

/* ---------- StatusCard ---------- */

const STATUS: Record<Level, { box: string; label: string; labelText: string; heading: string; body: string }> = {
  good: {
    box: "bg-good-bg border-good-border",
    label: "ALL GOOD",
    labelText: "text-good",
    heading: "text-good-strong",
    body: "text-good",
  },
  watch: {
    box: "bg-watch-bg border-watch-border",
    label: "SOMETHING TO WATCH",
    labelText: "text-watch-label",
    heading: "text-watch-heading",
    body: "text-watch-body",
  },
  urgent: {
    box: "bg-danger border-danger",
    label: "URGENT",
    labelText: "text-danger-lighter",
    heading: "text-white",
    body: "text-danger-light",
  },
};

export function StatusCard({
  level,
  headline,
  sub,
  parent,
  family,
  viewer,
}: {
  level: Level;
  headline: string;
  sub: string;
  parent: Parent;
  family: FamilyMember[];
  viewer: FamilyMember;
}) {
  const s = STATUS[level];
  const now = useNow(30_000);
  const next = now === null ? null : nextCall(parent.call_time, parent.tz, now);
  const others = family.filter((f) => f !== viewer);
  return (
    <section aria-label="Status" className={`flex flex-col gap-4 rounded-2xl border p-6 ${s.box}`}>
      <div className="flex flex-col gap-1">
        <p className={`text-sm font-bold tracking-wide ${s.labelText}`}>{s.label}</p>
        <h2 className={`text-2xl font-bold ${s.heading}`}>{headline}</h2>
        <p className={s.body}>{sub}</p>
      </div>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-xl bg-surface px-4 py-3 text-body">
        <span className="font-semibold text-ink">Next check-in</span>
        {next ? (
          <span className="tabular">
            · {next.tonight ? "Tonight" : "Tomorrow"} {clock(next.at, parent.tz)} her time · {clock(next.at, viewer.tz)} for
            you
            {others.map((o) => ` · ${clock(next.at, o.tz)} for ${o.name}`).join("")}
          </span>
        ) : (
          <span>· daily at {parent.call_time} her time</span>
        )}
      </div>
    </section>
  );
}

/* ---------- ThreadCard ---------- */

export function threadPill(t: Thread, today: string): { tone: PillTone; label: string } {
  if (t.kind === "child_question") return { tone: "info", label: `Asked by ${t.created_by ?? "family"}` };
  if (t.kind === "personal") return { tone: "neutral", label: "Personal" };
  if (t.status === "resolved") return { tone: "neutral", label: "Resolved" };
  const day = t.first_seen ? daysBetween(t.first_seen, today) + 1 : null;
  switch (t.last_status) {
    case "improving":
      return { tone: "good", label: `Improving${day ? ` · day ${day}` : ""}` };
    case "fine":
      return { tone: "good", label: "Fine" };
    case "worse":
      return { tone: "watch", label: `Worse${day ? ` · day ${day}` : ""}` };
    case "same":
      return { tone: "watch", label: `No change${day ? ` · day ${day}` : ""}` };
    default:
      return { tone: "watch", label: t.created_by === "band" ? "New · from band" : "New" };
  }
}

export function ThreadCard({ thread: t, today }: { thread: Thread; today: string }) {
  const pill = threadPill(t, today);
  const latest = [...t.history].reverse().find((h) => h.source === "call") ?? t.history.at(-1);
  const isQuote = latest?.source === "call" && latest.status !== "new";
  const followup =
    t.next_followup && t.next_followup > today
      ? `Next follow-up: ${weekdayOf(t.next_followup)}'s call`
      : t.next_followup === today
        ? "Asking on tonight's call"
        : null;
  const fallback =
    t.kind === "child_question" ? "FarCare will ask on tonight's call." : t.kind === "personal" ? "Something to talk about." : null;

  return (
    <li>
      <Link
        href={`/threads/${t.id}`}
        className="group flex items-start gap-3 rounded-xl border border-border bg-inset p-4 hover:border-primary"
      >
        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-semibold text-ink group-hover:text-primary">{t.topic}</h3>
            <StatusPill tone={pill.tone}>{pill.label}</StatusPill>
          </div>
          {latest ? (
            <p className="text-body">{isQuote ? <>&ldquo;{latest.note}&rdquo;</> : latest.note}</p>
          ) : (
            fallback && <p className="text-muted">{fallback}</p>
          )}
          {followup && <p className="text-sm text-muted">{followup}</p>}
        </div>
        <ChevronRight size={18} className="mt-1 shrink-0 text-muted group-hover:text-primary" aria-hidden="true" />
      </Link>
    </li>
  );
}

/* ---------- VitalTile ---------- */

const VITAL_ICON: Record<string, LucideIcon> = {
  sleep_min: Moon,
  steps: Footprints,
  resting_hr: HeartPulse,
  spo2: Droplet,
  bp: Gauge,
};

function formatVital(metric: string, v: number | string, unit?: string): string {
  if (typeof v === "string") return unit ? `${v} ${unit}` : v;
  if (metric === "sleep_min") return hoursMinutes(v);
  if (metric === "steps") return v.toLocaleString("en-US");
  if (unit === "%") return `${v}%`;
  return unit ? `${v} ${unit}` : String(v);
}

export function VitalTile({ vital: v }: { vital: Vital }) {
  const Icon = VITAL_ICON[v.metric] ?? Watch;
  const watch = v.level !== "good";
  return (
    <li
      className={`flex flex-col gap-1 rounded-xl border p-4 ${
        watch ? "border-watch-border bg-watch-bg" : "border-border bg-inset"
      }`}
    >
      <p className={`flex items-center gap-2 text-sm font-medium ${watch ? "text-watch-label" : "text-muted"}`}>
        <Icon size={16} aria-hidden="true" />
        {v.label}
      </p>
      <p className={`tabular text-2xl font-bold ${watch ? "text-watch-heading" : "text-ink"}`}>
        {formatVital(v.metric, v.value, v.unit)}
      </p>
      <p className={`text-sm ${watch ? "text-watch-body" : "text-muted"}`}>
        normal {formatVital(v.metric, v.normal, v.unit)}
        {watch && <span className="sr-only"> (worth watching)</span>}
      </p>
    </li>
  );
}

/* ---------- MedDots ---------- */

export function MedDots({ taken, today }: { taken: boolean[]; today: string }) {
  const days = taken.map((_, i) => addDays(today, i - (taken.length - 1)));
  const missed = days.filter((_, i) => !taken[i]);
  const count = taken.filter(Boolean).length;
  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm font-semibold text-ink">BP medicine, last {taken.length} days</p>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <ol className="flex gap-2" aria-label="Medicine taken by day">
          {days.map((d, i) => (
            <li key={d} className="flex flex-col items-center gap-1">
              <span
                className={`size-5 rounded-full ${taken[i] ? "bg-primary" : "border-2 border-danger bg-surface"}`}
                aria-hidden="true"
              />
              <span className="text-xs text-muted">{weekdayOf(d).slice(0, 1)}</span>
              <span className="sr-only">
                {weekdayOf(d)}: {taken[i] ? "taken" : "missed"}
              </span>
            </li>
          ))}
        </ol>
        <p className="text-body">
          <span className="font-semibold text-ink">
            {count} of {taken.length}
          </span>
          {missed.length > 0 && ` · missed ${missed.map(weekdayOf).join(", ")}`}
        </p>
      </div>
    </div>
  );
}

/* ---------- Timeline ---------- */

export type TimelineItem = {
  key: string;
  date: string;
  source: string;
  sourceIcon: LucideIcon;
  pill?: { tone: PillTone; label: string };
  body: ReactNode;
  scheduled?: boolean;
};

export function Timeline({ items }: { items: TimelineItem[] }) {
  return (
    <ol className="flex flex-col">
      {items.map((it, i) => {
        const last = i === items.length - 1;
        return (
          <li key={it.key} className="grid grid-cols-[1.25rem_1fr] gap-x-4">
            <span className="flex flex-col items-center" aria-hidden="true">
              <span
                className={`mt-1 size-4 shrink-0 rounded-full ${
                  it.scheduled ? "border-2 border-dashed border-muted bg-surface" : "bg-primary"
                }`}
              />
              {!last && <span className="w-0.5 flex-1 bg-border" />}
            </span>
            <div className={`flex min-w-0 flex-col gap-2 ${last ? "" : "pb-7"}`}>
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-semibold text-ink">{it.date}</span>
                <span className="inline-flex items-center gap-1 text-muted">
                  <it.sourceIcon size={14} aria-hidden="true" />
                  {it.source}
                </span>
                {it.pill && <StatusPill tone={it.pill.tone}>{it.pill.label}</StatusPill>}
              </div>
              <div className="text-body">{it.body}</div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export const sourceIcon = (source: string): LucideIcon => (source === "band" ? Watch : Phone);
export const dayLabel = shortDate;

/* ---------- ChatBubble ---------- */

export function ChatBubble({ line, nickname }: { line: TranscriptLine; nickname: string }) {
  const ama = line.role === "ama";
  return (
    <li className={`flex ${ama ? "justify-end" : "justify-start"}`}>
      <div
        className={`flex max-w-[85%] flex-col gap-1 rounded-2xl px-4 py-3 sm:max-w-[75%] ${
          ama ? "rounded-br-md bg-primary-tint" : "rounded-bl-md bg-neutral-bg"
        }`}
      >
        <p className="text-xs font-semibold text-muted">{ama ? nickname : "FarCare"}</p>
        {line.ne && <Ne className="text-lg leading-relaxed text-ink">{line.ne}</Ne>}
        <p className={`text-sm ${ama ? "text-on-tint" : "text-neutral"}`}>{line.en}</p>
      </div>
    </li>
  );
}

/* ---------- AgendaList ---------- */

export function AgendaList({ items }: { items: AgendaItem[] }) {
  return (
    <ol className="flex flex-col gap-1.5">
      {items.map((it) => (
        <li
          key={it.label}
          aria-current={it.current ? "step" : undefined}
          className={`flex min-h-11 items-center gap-3 rounded-[10px] px-3 ${
            it.current ? "bg-primary-tint font-semibold text-on-tint" : it.done ? "text-muted" : "text-body"
          }`}
        >
          <span
            className={`grid size-5 shrink-0 place-items-center rounded-md border ${
              it.done ? "border-primary bg-primary text-white" : it.current ? "border-primary bg-surface" : "border-border bg-surface"
            }`}
            aria-hidden="true"
          >
            {it.done && <Check size={14} strokeWidth={3} />}
          </span>
          <span className={it.done ? "line-through decoration-muted/50" : ""}>{it.label}</span>
          <span className="sr-only">{it.done ? "(done)" : it.current ? "(now)" : "(coming up)"}</span>
        </li>
      ))}
    </ol>
  );
}

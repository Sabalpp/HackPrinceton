import Link from "next/link";
import { Activity, Bell, HandHeart, MessageCircleHeart, MessageSquareText, RotateCcw, ShieldAlert, ShieldCheck, type LucideIcon } from "lucide-react";
import type { TimelineEvent } from "@/lib/api";
import { TimeAgo } from "@/components/ui/time";

type Look = { Icon: LucideIcon; chip: string; label: string; href?: string };

/** Icon + colour by kind prefix (care_request.*, incident.*, checkin.*). Unknown kinds are generic. */
export function eventLook(e: TimelineEvent): Look {
  const k = e.kind;
  if (k.startsWith("incident.")) {
    if (k === "incident.resolved") return { Icon: ShieldCheck, chip: "bg-good-bg text-good", label: "Safety", href: "/safety" };
    if (k === "incident.responder_en_route") return { Icon: ShieldAlert, chip: "bg-primary-tint text-primary", label: "Safety", href: "/safety" };
    if (k === "incident.opened") return { Icon: ShieldAlert, chip: "bg-watch-bg text-watch-label", label: "Safety", href: "/safety" };
    return { Icon: ShieldAlert, chip: "bg-danger-tint text-danger-ink", label: "Safety", href: "/safety" };
  }
  if (k.startsWith("care_request.")) {
    const bad = k.endsWith("delivery_failed") || k.endsWith("more_help_needed");
    return { Icon: HandHeart, chip: bad ? "bg-watch-bg text-watch-label" : "bg-primary-tint text-primary", label: "Help request", href: "/requests" };
  }
  if (k.startsWith("checkin.")) return { Icon: MessageCircleHeart, chip: "bg-coral-tint text-coral-ink", label: "Check-in", href: "/" };
  if (k.startsWith("fall.")) return { Icon: Activity, chip: "bg-neutral-bg text-neutral", label: "Sensor" };
  if (k.startsWith("agent.")) return { Icon: MessageSquareText, chip: "bg-info-bg text-info", label: "Companion" };
  if (k.startsWith("demo.")) return { Icon: RotateCcw, chip: "bg-neutral-bg text-neutral", label: "Demo" };
  return { Icon: Bell, chip: "bg-neutral-bg text-neutral", label: "Update" };
}

/** Friendlier wording for system events whose stored summary is technical. */
const PLAIN: Record<string, string> = {
  "health.synced": "New daily readings arrived from the watch",
  "health.pattern_noted": "FarCare noticed a change from the usual and suggested a check-in",
};

export function FeedItem({ event: e, isNew = false, last = false }: { event: TimelineEvent; isNew?: boolean; last?: boolean }) {
  const look = eventLook(e);
  const body = (
    <>
      <span className="flex flex-col items-center" aria-hidden="true">
        <span className={`grid size-9 shrink-0 place-items-center rounded-full ${look.chip}`}>
          <look.Icon size={17} />
        </span>
        {!last && <span className="w-px flex-1 bg-border" />}
      </span>
      <span className={`flex min-w-0 flex-col gap-0.5 ${last ? "" : "pb-5"}`}>
        <span className="text-ink">{PLAIN[e.kind] ?? e.summary}</span>
        <span className="flex flex-wrap items-center gap-x-2 text-sm text-muted">
          <span>{look.label}</span>
          <span aria-hidden="true">·</span>
          <TimeAgo iso={e.created_at} />
          {isNew && <span className="rounded-full bg-sun/20 px-2 text-xs font-bold text-coral-ink">New</span>}
        </span>
      </span>
    </>
  );
  return (
    <li className="grid grid-cols-[2.25rem_1fr] gap-x-3">
      {look.href ? (
        <Link href={look.href} className="col-span-2 grid grid-cols-[2.25rem_1fr] gap-x-3 rounded-lg hover:bg-inset/60">
          {body}
        </Link>
      ) : (
        body
      )}
    </li>
  );
}

export function Feed({ events, newIds }: { events: TimelineEvent[]; newIds?: Set<number> }) {
  return (
    <ol className="flex flex-col">
      {events.map((e, i) => (
        <FeedItem key={e.id} event={e} isNew={newIds?.has(e.id)} last={i === events.length - 1} />
      ))}
    </ol>
  );
}

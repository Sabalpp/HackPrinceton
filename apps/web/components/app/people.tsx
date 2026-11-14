import { CircleCheck, Shield } from "lucide-react";
import type { CareCircleMember, CheckIn } from "@/lib/api";
import { CATEGORY_LABEL, relationshipLabel } from "@/lib/format";
import { TimeAgo } from "@/components/ui/time";
import { Avatar, CATEGORY_ICON, EmptyBlock } from "./kit";

export function MemberCard({ member: m, rank, detailed = false }: { member: CareCircleMember; rank?: number; detailed?: boolean }) {
  return (
    <li className="flex gap-4 rounded-2xl border border-border bg-surface p-5">
      <Avatar name={m.display_name} size="lg" />
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-lg">
            <span className="font-bold text-ink">{m.display_name}</span>
            <span className="text-muted"> · {relationshipLabel(m.relationship)}</span>
          </p>
          {m.reachable ? (
            <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-good">
              <CircleCheck size={16} aria-hidden="true" /> Available for requests
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-watch-label">
              <span className="size-2 rounded-full bg-sun" aria-hidden="true" /> Not reachable yet
            </span>
          )}
        </div>
        {!m.reachable && <p className="text-sm text-body">This helper still needs an approved app account or connected messaging access.</p>}
        {m.reachable && detailed && <p className="text-sm text-body">Requests can be handled in the helper&apos;s app. Phone messages require a connected messaging account.</p>}
        <ul className="flex flex-wrap gap-1.5">
          {m.categories.map((c) => {
            const Icon = CATEGORY_ICON[c];
            return (
              <li key={c} className="inline-flex items-center gap-1.5 rounded-full bg-inset px-2.5 py-1 text-sm text-body">
                <Icon size={14} aria-hidden="true" />
                {CATEGORY_LABEL[c]}
              </li>
            );
          })}
        </ul>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
          {m.emergency_contact && (
            <span className="inline-flex items-center gap-1.5 font-semibold text-primary">
              <Shield size={15} aria-hidden="true" /> Emergency contact
            </span>
          )}
          {detailed && rank !== undefined && <span className="text-muted">Asked {ordinal(rank)} when help is needed</span>}
        </div>
      </div>
    </li>
  );
}

const ordinal = (n: number) => (n === 1 ? "first" : n === 2 ? "second" : n === 3 ? "third" : `#${n}`);

const MOOD: Record<CheckIn["mood"], string> = { good: "Good", okay: "Okay", low: "Low", unknown: "Not shared" };
const ENERGY: Record<CheckIn["energy"], string> = { high: "High", normal: "Normal", low: "Low", unknown: "Not shared" };

export function CheckInCard({ checkin, shared, name }: { checkin: CheckIn | null; shared: boolean; name: string }) {
  if (!checkin) {
    return shared ? (
      <EmptyBlock title="No check-in yet">{name}&apos;s next check-in will show up here.</EmptyBlock>
    ) : (
      <EmptyBlock title={`${name} isn't sharing check-ins`}>That&apos;s {name}&apos;s choice. They can change it from their parent screen&apos;s sharing settings.</EmptyBlock>
    );
  }
  const low = checkin.mood === "low" || checkin.energy === "low";
  return (
    <div className="flex flex-col gap-4 rounded-2xl border border-border bg-surface p-5">
      <div className="flex items-center gap-3">
        <Avatar name={name} tone="sun" />
        <p className="text-sm text-muted">
          {name} said, <TimeAgo iso={checkin.created_at} />
        </p>
      </div>
      <blockquote className="text-xl leading-snug font-medium text-ink">&ldquo;{checkin.summary}&rdquo;</blockquote>
      <dl className="grid grid-cols-2 gap-2">
        {[
          ["Mood", MOOD[checkin.mood], checkin.mood === "low"],
          ["Energy", ENERGY[checkin.energy], checkin.energy === "low"],
        ].map(([k, v, isLow]) => (
          <div key={k as string} className={`rounded-xl px-3 py-2 ${isLow ? "bg-watch-bg" : "bg-inset"}`}>
            <dt className={`text-sm ${isLow ? "text-watch-label" : "text-muted"}`}>{k}</dt>
            <dd className={`font-bold ${isLow ? "text-watch-heading" : "text-ink"}`}>{v}</dd>
          </div>
        ))}
      </dl>
      {low && <p className="text-sm text-body">A call from you might help. It&apos;s a good time to check in.</p>}
    </div>
  );
}

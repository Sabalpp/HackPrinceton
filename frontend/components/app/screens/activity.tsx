"use client";

import { useMemo, useState } from "react";
import { Activity } from "lucide-react";
import type { TimelineEvent } from "@/lib/api";
import { useDashboard } from "@/lib/dashboard";
import { Ready } from "../shell";
import { EmptyBlock, PageHeader } from "../kit";
import { Feed } from "../feed";

type Filter = "all" | "care_request" | "incident" | "checkin";
const FILTERS: [Filter, string][] = [
  ["all", "Everything"],
  ["care_request", "Help requests"],
  ["incident", "Safety"],
  ["checkin", "Check-ins"],
];

const dayKey = (iso: string) =>
  new Intl.DateTimeFormat("en-US", { weekday: "long", month: "long", day: "numeric" }).format(new Date(iso));

export function ActivityScreen() {
  return <Ready>{(r) => <ActivityView name={r.overview.parent.display_name} />}</Ready>;
}

function ActivityView({ name }: { name: string }) {
  const { timeline, connection } = useDashboard();
  const [filter, setFilter] = useState<Filter>("all");
  // Events that arrive after the page opened get a "New" tag.
  const [firstSeen] = useState(() => timeline[0]?.id ?? 0);
  const newIds = new Set(timeline.filter((e) => e.id > firstSeen).map((e) => e.id));

  const groups = useMemo(() => {
    const list = timeline.filter((e) => filter === "all" || e.kind.startsWith(`${filter}.`));
    const out: [string, TimelineEvent[]][] = [];
    for (const e of list) {
      const k = dayKey(e.created_at);
      const g = out.find(([d]) => d === k);
      if (g) g[1].push(e);
      else out.push([k, [e]]);
    }
    return out;
  }, [timeline, filter]);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Activity"
        title="Everything, as it happens"
        description={`Requests, replies, alerts and check-ins for ${name}. ${connection === "live" || connection === "mock" ? "This page updates live." : ""}`}
      />
      <div role="tablist" aria-label="Filter activity" className="flex flex-wrap gap-2">
        {FILTERS.map(([f, label]) => (
          <button
            key={f}
            type="button"
            role="tab"
            aria-selected={filter === f}
            onClick={() => setFilter(f)}
            className={`min-h-11 rounded-full px-4 text-sm font-semibold ${
              filter === f ? "bg-primary text-on-primary" : "border border-border bg-surface text-body hover:border-primary"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      {groups.length === 0 ? (
        <EmptyBlock title="Nothing here yet" icon={Activity}>
          New activity shows up the moment it happens.
        </EmptyBlock>
      ) : (
        groups.map(([day, events]) => (
          <section key={day} className="flex flex-col gap-3" aria-label={day}>
            <h2 className="text-base font-semibold text-ink">{day}</h2>
            <div className="rounded-2xl border border-border bg-surface p-5 shadow-card">
              <Feed events={events} newIds={newIds} />
            </div>
          </section>
        ))
      )}
    </div>
  );
}

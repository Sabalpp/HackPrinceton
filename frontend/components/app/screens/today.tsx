"use client";

import Link from "next/link";
import { useMemo } from "react";
import { ArrowRight, CircleCheck, Clock, HandHeart, HeartPulse, MessageCircleHeart, ShieldAlert, Users, type LucideIcon } from "lucide-react";
import type { CareCircleMember, HealthSummary, MetricTrend, ParentOverview } from "@/lib/api";
import { isRequestOpen, sortRequests, summarize, type Summary } from "@/lib/summary";
import { relationshipLabel } from "@/lib/format";
import { useNow } from "@/lib/use-now";
import { useSession } from "@/lib/session";
import { SimulatedPill } from "@/components/farcare/ui";
import { LiveSensorPanel } from "@/components/portal/live-sensors";
import { FarCareChat } from "@/components/portal/chat";
import { TwoClocks } from "@/components/ui/time";
import { Ready } from "../shell";
import { Avatar, Card, CardLink, EmptyBlock, PageHeader } from "../kit";
import { IncidentPanel } from "../incident";
import { RequestCard } from "../request";
import { METRIC } from "../health";
import { CheckInCard } from "../people";

export function TodayScreen() {
  return <Ready>{(r) => <Today overview={r.overview} viewerName={r.me.person.display_name} />}</Ready>;
}

function Greeting({ name }: { name: string }) {
  const now = useNow(60_000);
  const h = now === null ? 9 : new Date(now).getHours();
  return <>{h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"}, {name}</>;
}

const BANNER: Record<Summary["tone"], { box: string; icon: string; Icon: LucideIcon }> = {
  done: { box: "border-good-border bg-good-bg", icon: "text-good", Icon: CircleCheck },
  neutral: { box: "border-good-border bg-good-bg", icon: "text-good", Icon: CircleCheck },
  coming: { box: "border-primary/30 bg-primary-tint", icon: "text-primary", Icon: HandHeart },
  waiting: { box: "border-watch-border bg-watch-bg", icon: "text-watch-label", Icon: Clock },
  urgent: { box: "border-danger/40 bg-danger-tint", icon: "text-danger-ink", Icon: ShieldAlert },
};

/** One sentence that answers "is everything okay?", with the single most useful next step. */
function StatusBanner({ o }: { o: ParentOverview }) {
  const s = useMemo(() => summarize(o), [o]);
  const look = BANNER[s.tone];
  const alert = o.active_incidents.length > 0;
  return (
    <section aria-label="Right now" className={`flex flex-wrap items-center gap-x-4 gap-y-3 rounded-2xl border p-5 ${look.box}`}>
      <look.Icon size={26} className={`shrink-0 ${look.icon}`} aria-hidden="true" />
      <div className="min-w-[12rem] flex-1">
        <p className="text-lg font-semibold text-ink">{s.title}</p>
        {s.detail && <p className="text-body">{s.detail}</p>}
      </div>
      {alert ? (
        <Link href="/safety" className="inline-flex min-h-11 items-center gap-2 rounded-[10px] bg-danger px-4 font-semibold text-white hover:brightness-110">
          Open the safety alert <ArrowRight size={18} aria-hidden="true" />
        </Link>
      ) : (
        <Link href="/activity" className="inline-flex min-h-11 items-center gap-2 rounded-[10px] border border-border bg-surface px-4 font-semibold text-ink hover:border-primary hover:text-primary">
          Today&apos;s activity <ArrowRight size={18} aria-hidden="true" />
        </Link>
      )}
    </section>
  );
}

/** One metric: the last 5 days next to the person's own usual. */
function TrendStat({ t }: { t: MetricTrend }) {
  const m = METRIC[t.metric];
  const has = t.recent !== null && Number.isFinite(t.recent) && t.recent >= 0;
  const usual = t.baseline !== null && Number.isFinite(t.baseline) && t.baseline > 0 ? t.baseline : null;
  let note = "Not enough data yet";
  if (has && t.direction === "insufficient_data") note = "Learning the usual";
  else if (has && usual !== null) {
    note = t.metric === "resting_heart_rate"
      ? `Usual ${m.fmt(usual)} bpm`
      : `${Math.round((t.recent! / usual) * 100)}% of usual`;
  }
  return (
    <div className="flex min-w-0 flex-col gap-1 p-5">
      <p className="flex items-center gap-2 text-sm text-muted"><m.Icon size={16} aria-hidden="true" />{m.label}</p>
      <p className="tabular text-2xl font-bold text-ink">
        {has ? m.fmt(t.recent!) : "—"}
        {has && m.unit && <span className="ml-1 text-sm font-medium text-muted">{m.unit}</span>}
      </p>
      <p className={`text-sm ${t.notable ? "font-semibold text-watch-label" : "text-muted"}`}>{t.notable ? `Changed · ${note}` : note}</p>
    </div>
  );
}

function HealthCard({ name, health }: { name: string; health: HealthSummary | null }) {
  if (!health) {
    return (
      <Card title="Health this week" icon={HeartPulse}>
        <EmptyBlock title={`${name} hasn't shared health data`}>
          That&apos;s {name}&apos;s choice. They can change it in their sharing settings.
        </EmptyBlock>
      </Card>
    );
  }
  return (
    <Card
      title="Last 5 days vs usual"
      icon={HeartPulse}
      action={
        <>
          {health.data_mode === "simulated" && <SimulatedPill />}
          {health.data_mode === "mixed" && <span className="rounded-full bg-watch-bg px-3 py-1 text-xs font-semibold text-watch-heading">Mixed sources</span>}
          <CardLink href="/health">Health details</CardLink>
        </>
      }
      bodyClassName="grid grid-cols-1 divide-y divide-border sm:grid-cols-3 sm:divide-x sm:divide-y-0"
    >
      {health.data_mode === "none" ? (
        <p className="p-5 text-muted sm:col-span-3">No readings yet.</p>
      ) : (
        health.trends.map((t) => <TrendStat key={t.metric} t={t} />)
      )}
    </Card>
  );
}

function HelperRow({ member: m }: { member: CareCircleMember }) {
  return (
    <li className="flex items-center gap-3 px-5 py-3.5">
      <Avatar name={m.display_name} size="sm" />
      <div className="min-w-0 flex-1">
        <p className="font-semibold text-ink">{m.display_name}</p>
        <p className="text-sm text-muted">{relationshipLabel(m.relationship)}</p>
      </div>
      {m.reachable ? (
        <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-good"><CircleCheck size={16} aria-hidden="true" />Ready</span>
      ) : (
        <span className="text-sm text-muted">Not reachable yet</span>
      )}
    </li>
  );
}

function Today({ overview: o, viewerName }: { overview: ParentOverview; viewerName: string }) {
  const { session } = useSession();
  const isFamily = session?.person?.role === "child";
  const name = o.parent.display_name;
  const relOf = (id: string | null) => o.care_circle.find((m) => m.responder_id === id)?.relationship;
  const openIncidentIds = new Set(o.active_incidents.map((i) => i.incident_id));
  const requests = sortRequests(o.recent_requests).filter((r) => !r.incident_id || !openIncidentIds.has(r.incident_id));
  const shown = requests.slice(0, 3);
  const open = requests.filter(isRequestOpen).length;
  const helpers = [...o.care_circle].sort((a, b) => a.priority - b.priority);
  const ready = helpers.filter((m) => m.reachable).length;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader eyebrow={<Greeting name={viewerName} />} title={name} description={<TwoClocks timeZone={o.parent.timezone} />} />

      {/* During an alert the incident panel below is the status; don't say it twice. */}
      {o.active_incidents.length === 0 && <StatusBanner o={o} />}

      {o.active_incidents.map((inc) => (
        <IncidentPanel
          key={inc.incident_id}
          incident={inc}
          parent={o.parent}
          requests={o.recent_requests.filter((r) => r.incident_id === inc.incident_id)}
          relationshipOf={relOf}
        />
      ))}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="flex min-w-0 flex-col gap-6 lg:col-span-2">
          <HealthCard name={name} health={o.health} />

          <Card
            title="Help requests"
            icon={HandHeart}
            description={open ? `${open} open` : "None open"}
            action={<CardLink href="/requests">All requests</CardLink>}
          >
            {shown.length ? (
              <div className="divide-y divide-border">
                {shown.map((r) => <RequestCard key={r.request_id} request={r} parentName={name} quiet={!isRequestOpen(r)} flat />)}
              </div>
            ) : (
              <EmptyBlock title="No help requests yet" icon={HandHeart}>
                When {name} asks for help with groceries, rides or anything else, it shows up here.
              </EmptyBlock>
            )}
          </Card>
        </div>

        <div className="flex min-w-0 flex-col gap-6">
          {o.permissions.share_health && <LiveSensorPanel parentId={o.parent.parent_id} compact />}

          <Card title="Latest check-in" icon={MessageCircleHeart}>
            <CheckInCard checkin={o.latest_checkin} shared={o.permissions.share_checkins ?? false} name={name} flat />
          </Card>

          <Card
            title="Helpers"
            icon={Users}
            description={`${ready} of ${helpers.length} ready`}
            action={<CardLink href="/circle">Care Circle</CardLink>}
            bodyClassName=""
          >
            {helpers.length ? (
              <ul className="divide-y divide-border">{helpers.map((m) => <HelperRow key={m.responder_id} member={m} />)}</ul>
            ) : (
              <p className="p-5 text-muted">No helpers yet.</p>
            )}
          </Card>
        </div>
      </div>

      {isFamily && <FarCareChat role="child" />}
    </div>
  );
}

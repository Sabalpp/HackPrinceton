"use client";

import Link from "next/link";
import { useMemo } from "react";
import { ArrowRight, Footprints, HandHeart, HeartPulse, Moon, Users } from "lucide-react";
import type { HealthSummary, MetricTrend, ParentOverview, TimelineEvent } from "@/lib/api";
import { useDashboard } from "@/lib/dashboard";
import { isRequestOpen, sortRequests, summarize, type Summary } from "@/lib/summary";
import { useNow } from "@/lib/use-now";
import { SimulatedPill } from "@/components/farcare/ui";
import { LiveSensorPanel } from "@/components/portal/live-sensors";
import { TwoClocks } from "@/components/ui/time";
import { Ready } from "../shell";
import { EmptyBlock, SectionTitle } from "../kit";
import { IncidentPanel } from "../incident";
import { RequestCard } from "../request";
import { METRIC } from "../health";
import { Ring } from "../ring";
import { CheckInCard, MemberCard } from "../people";
import { Feed } from "../feed";

export function TodayScreen() {
  return <Ready>{(r) => <Today overview={r.overview} viewerName={r.me.person.display_name} />}</Ready>;
}

function Greeting({ name }: { name: string }) {
  const now = useNow(60_000);
  const h = now === null ? 9 : new Date(now).getHours();
  return <>{h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"}, {name}</>;
}

const TONE: Record<Summary["tone"], { frame: string; dot: string; label: string }> = {
  done: { frame: "border-border bg-surface", dot: "bg-primary", label: "text-primary" },
  neutral: { frame: "border-border bg-surface", dot: "bg-primary", label: "text-primary" },
  coming: { frame: "border-primary/40 bg-surface", dot: "bg-primary animate-pulse", label: "text-primary" },
  waiting: { frame: "border-watch-border bg-surface", dot: "bg-watch-label", label: "text-watch-label" },
  urgent: { frame: "border-danger/60 bg-danger-tint", dot: "bg-danger-ink animate-pulse", label: "text-danger-ink" },
};

const RING_ICON = { steps: Footprints, sleep_minutes: Moon, resting_heart_rate: HeartPulse } as const;

/** One ring per metric: the last 5 days as a share of the parent's own usual. */
function TrendRing({ t }: { t: MetricTrend }) {
  const m = METRIC[t.metric];
  if (t.recent === null || !Number.isFinite(t.recent) || t.recent < 0) {
    return <Ring value={0} tone="muted" center="–" label={m.label} caption="Not enough data" Icon={RING_ICON[t.metric]} />;
  }
  if (t.baseline === null || !Number.isFinite(t.baseline) || t.baseline <= 0) {
    return <Ring value={0} tone="muted" center={m.fmt(t.recent)} label={m.label} caption="No usual baseline" Icon={RING_ICON[t.metric]} />;
  }
  const ratio = t.recent / t.baseline;
  if (!Number.isFinite(ratio)) {
    return <Ring value={0} tone="muted" center={m.fmt(t.recent)} label={m.label} caption="Comparison unavailable" Icon={RING_ICON[t.metric]} />;
  }
  const tone = t.notable ? "watch" : "primary";
  if (t.metric === "resting_heart_rate") {
    return (
      <Ring value={Math.min(1, ratio)} tone={tone} center={`${Math.round(t.recent)}`} unit="bpm" label="Resting HR" caption={`usual ${Math.round(t.baseline)}`} Icon={RING_ICON[t.metric]} />
    );
  }
  return (
    <Ring
      value={ratio}
      tone={tone}
      center={`${Math.round(ratio * 100)}%`}
      unit="of usual"
      label={t.metric === "steps" ? "Steps" : "Sleep"}
      caption={m.fmt(t.recent)}
      Icon={RING_ICON[t.metric]}
    />
  );
}

function Hero({ o, viewerName }: { o: ParentOverview; viewerName: string }) {
  const s = useMemo(() => summarize(o), [o]);
  const look = TONE[s.tone];
  const open = o.recent_requests.filter(isRequestOpen).length;
  const reachable = o.care_circle.filter((m) => m.reachable).length;
  const alert = o.active_incidents.length > 0;
  const health: HealthSummary | null = o.health;

  return (
    <section aria-label="Right now" className={`overflow-hidden rounded-3xl border ${look.frame}`}>
      <div className="grid gap-8 p-6 sm:p-8 xl:grid-cols-[minmax(0,1fr)_auto] xl:items-center">
        <div className="flex min-w-0 flex-col gap-6">
          <div className="flex flex-col gap-1">
            <p className="text-xs font-bold uppercase tracking-[0.16em] text-muted">
              <Greeting name={viewerName} />
            </p>
            <h1 className="text-5xl font-extrabold tracking-tight text-ink sm:text-6xl">{o.parent.display_name}</h1>
            <div className="text-body">
              <TwoClocks timeZone={o.parent.timezone} />
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <p className={`flex items-center gap-2 text-xs font-bold uppercase tracking-[0.16em] ${look.label}`}>
              <span className={`size-2 rounded-full ${look.dot}`} aria-hidden="true" />
              Right now
            </p>
            <p className="text-2xl font-bold leading-tight text-ink sm:text-3xl">{s.title}</p>
            {s.detail && <p className="text-body">{s.detail}</p>}
          </div>

          <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center">
            {alert ? (
              <Link href="/safety" className="col-span-2 inline-flex min-h-12 items-center justify-center gap-2 rounded-[10px] bg-danger px-4 font-bold text-white hover:brightness-110">
                Open the safety alert <ArrowRight size={18} aria-hidden="true" />
              </Link>
            ) : (
              <Link href="/activity" className="col-span-2 inline-flex min-h-12 items-center justify-center gap-2 rounded-[10px] bg-primary px-4 font-bold text-on-primary hover:bg-primary-hover">
                Today&apos;s activity <ArrowRight size={18} aria-hidden="true" />
              </Link>
            )}
            <Stat Icon={HandHeart} value={open} label={`open request${open === 1 ? "" : "s"}`} />
            <Stat Icon={Users} value={`${reachable}/${o.care_circle.length}`} label="reachable" />
          </div>
        </div>

        {health ? (
          <div className="flex flex-col items-center gap-4 rounded-2xl border border-border bg-inset/60 p-5">
            <div className="flex w-full flex-wrap items-center justify-between gap-3">
              <p className="text-xs font-bold uppercase tracking-[0.16em] text-muted">Last 5 days vs usual</p>
              {health.data_mode === "simulated" && <SimulatedPill />}
              {health.data_mode === "mixed" && <span className="rounded-full bg-watch-bg px-3 py-1 text-sm font-semibold text-watch-heading">Mixed sources</span>}
              {health.data_mode === "none" && <span className="text-sm text-muted">No readings yet</span>}
            </div>
            <div className="grid grid-cols-3 gap-3 sm:gap-6">
              {health.trends.map((t) => (
                <TrendRing key={t.metric} t={t} />
              ))}
            </div>
            <Link href="/health" className="inline-flex min-h-11 items-center gap-1 text-sm font-semibold text-primary hover:underline">
              Health trends <ArrowRight size={15} aria-hidden="true" />
            </Link>
          </div>
        ) : (
          <EmptyBlock title={`${o.parent.display_name} hasn't shared health data`} icon={HeartPulse}>
            That&apos;s {o.parent.display_name}&apos;s choice. They can manage it from the parent screen&apos;s sharing settings.
          </EmptyBlock>
        )}
      </div>
    </section>
  );
}

function Stat({ Icon, value, label }: { Icon: typeof Users; value: number | string; label: string }) {
  return (
    <span className="inline-flex min-h-11 items-center gap-2 rounded-[10px] border border-border bg-inset px-3 text-sm text-body">
      <Icon size={16} className="text-primary" aria-hidden="true" />
      <span className="tabular font-bold text-ink">{value}</span> {label}
    </span>
  );
}

function Today({ overview: o, viewerName }: { overview: ParentOverview; viewerName: string }) {
  const { timeline } = useDashboard();
  const name = o.parent.display_name;
  const relOf = (id: string | null) => o.care_circle.find((m) => m.responder_id === id)?.relationship;
  const openIncidentIds = new Set(o.active_incidents.map((i) => i.incident_id));
  const requests = sortRequests(o.recent_requests).filter((r) => !r.incident_id || !openIncidentIds.has(r.incident_id));
  const shown = requests.slice(0, 3);
  const recent: TimelineEvent[] = timeline.slice(0, 6);
  const more = (href: string, label: string) => (
    <Link href={href} className="inline-flex min-h-11 items-center gap-1 text-sm font-semibold text-primary hover:underline">
      {label} <ArrowRight size={15} aria-hidden="true" />
    </Link>
  );

  return (
    <div className="flex flex-col gap-10">
      <Hero o={o} viewerName={viewerName} />

      {o.active_incidents.map((inc) => (
        <IncidentPanel
          key={inc.incident_id}
          incident={inc}
          parent={o.parent}
          requests={o.recent_requests.filter((r) => r.incident_id === inc.incident_id)}
          relationshipOf={relOf}
        />
      ))}

      {o.permissions.share_health && <LiveSensorPanel parentId={o.parent.parent_id} compact />}

      <div className="grid grid-cols-1 gap-10 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <section className="flex min-w-0 flex-col gap-4" aria-labelledby="req-title">
          <SectionTitle action={more("/requests", "All requests")}>
            <span id="req-title">Help requests</span>
          </SectionTitle>
          {shown.length ? (
            <div className="flex flex-col gap-3">
              {shown.map((r) => (
                <RequestCard key={r.request_id} request={r} parentName={name} quiet={!isRequestOpen(r)} />
              ))}
            </div>
          ) : (
            <EmptyBlock title="No help requests yet" icon={HandHeart}>
              When {name} texts FarCare for help with groceries, rides or anything else, it shows up here with who&apos;s
              going and when.
            </EmptyBlock>
          )}
        </section>

        <div className="flex min-w-0 flex-col gap-10">
          <section className="flex flex-col gap-4" aria-labelledby="chk-title">
            <SectionTitle>
              <span id="chk-title">Latest check-in</span>
            </SectionTitle>
            <CheckInCard checkin={o.latest_checkin} shared={o.permissions.share_checkins ?? false} name={name} />
          </section>

          <section className="flex flex-col gap-4" aria-labelledby="act-title">
            <SectionTitle action={more("/activity", "Everything")}>
              <span id="act-title">Recent activity</span>
            </SectionTitle>
            <div className="rounded-2xl border border-border bg-surface p-5">
              {recent.length ? <Feed events={recent} /> : <p className="text-body">Nothing yet today.</p>}
            </div>
          </section>
        </div>
      </div>

      <section className="flex flex-col gap-4" aria-labelledby="circle-title">
        <SectionTitle action={more("/circle", "Care Circle")}>
          <span id="circle-title">Who&apos;s nearby for {name}</span>
        </SectionTitle>
        <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {[...o.care_circle].sort((a, b) => a.priority - b.priority).map((m) => (
            <MemberCard key={m.responder_id} member={m} />
          ))}
        </ul>
      </section>
    </div>
  );
}

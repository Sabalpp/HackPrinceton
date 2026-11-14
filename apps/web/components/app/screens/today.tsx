"use client";

import Link from "next/link";
import { useMemo } from "react";
import { ArrowRight, CircleCheck, HandHeart, HeartPulse, ShieldAlert, Users } from "lucide-react";
import type { ParentOverview, TimelineEvent } from "@/lib/api";
import { useDashboard } from "@/lib/dashboard";
import { isRequestOpen, sortRequests, summarize, type Summary } from "@/lib/summary";
import { useNow } from "@/lib/use-now";
import { SimulatedPill } from "@/components/farcare/ui";
import { TwoClocks } from "@/components/ui/time";
import { Ready } from "../shell";
import { EmptyBlock, SectionTitle } from "../kit";
import { IncidentPanel } from "../incident";
import { RequestCard } from "../request";
import { TrendTile } from "../health";
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

const HERO: Record<Summary["tone"], { band: string; sub: string; chip: string; Icon: typeof CircleCheck }> = {
  done: { band: "bg-hero text-white", sub: "text-primary-tint", chip: "bg-white/12 text-white", Icon: CircleCheck },
  neutral: { band: "bg-hero text-white", sub: "text-primary-tint", chip: "bg-white/12 text-white", Icon: CircleCheck },
  coming: { band: "bg-hero text-white", sub: "text-primary-tint", chip: "bg-white/12 text-white", Icon: HandHeart },
  waiting: { band: "bg-watch-bg text-watch-heading border border-watch-border", sub: "text-watch-body", chip: "bg-surface/70 text-watch-heading", Icon: HeartPulse },
  urgent: { band: "bg-danger text-white", sub: "text-danger-light", chip: "bg-white/15 text-white", Icon: ShieldAlert },
};

/** Two people far apart, connected: the brand idea, drawn flat. */
function DistanceMotif({ light }: { light: boolean }) {
  const line = light ? "rgba(255,255,255,.55)" : "var(--color-watch-border)";
  return (
    <svg viewBox="0 0 220 120" className="pointer-events-none absolute right-2 top-2 hidden h-40 w-72 sm:block" aria-hidden="true">
      <path d="M30 92 C 80 10, 140 10, 172 52" fill="none" stroke={line} strokeWidth="2.5" strokeDasharray="3 7" strokeLinecap="round" />
      <circle cx="30" cy="92" r="14" fill="var(--color-sun)" />
      <circle cx="172" cy="52" r="20" fill={light ? "rgba(255,255,255,.9)" : "var(--color-primary)"} />
      <circle cx="172" cy="52" r="34" fill="none" stroke={line} strokeWidth="1.5" />
    </svg>
  );
}

function Hero({ o, viewerName }: { o: ParentOverview; viewerName: string }) {
  const s = useMemo(() => summarize(o), [o]);
  const look = HERO[s.tone];
  const light = s.tone !== "waiting";
  const open = o.recent_requests.filter(isRequestOpen).length;
  const reachable = o.care_circle.filter((m) => m.reachable).length;
  const alert = o.active_incidents.length > 0;
  return (
    <section aria-label="Right now" className={`relative overflow-hidden rounded-3xl p-6 sm:p-8 ${look.band}`}>
      <DistanceMotif light={light} />
      <div className="relative flex max-w-3xl flex-col gap-5">
        <div className="flex flex-col gap-1">
          <p className={`text-sm font-semibold ${look.sub}`}>
            <Greeting name={viewerName} />
          </p>
          <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">{o.parent.display_name}</h1>
          <div className={look.sub}>
            <TwoClocks timeZone={o.parent.timezone} />
          </div>
        </div>

        <div className="flex items-start gap-3">
          <look.Icon size={28} className="mt-0.5 shrink-0" aria-hidden="true" />
          <div>
            <p className={`text-xs font-bold uppercase tracking-wider ${look.sub}`}>Right now</p>
            <p className="text-2xl font-bold leading-tight sm:text-[1.75rem]">{s.title}</p>
            {s.detail && <p className={`mt-1 ${look.sub}`}>{s.detail}</p>}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {alert ? (
            <Link href="/safety" className="inline-flex min-h-11 items-center gap-2 rounded-[10px] bg-white px-4 font-bold text-danger hover:bg-danger-light">
              Open the safety alert <ArrowRight size={18} aria-hidden="true" />
            </Link>
          ) : (
            <Link
              href="/activity"
              className={`inline-flex min-h-11 items-center gap-2 rounded-[10px] px-4 font-semibold ${
                light ? "bg-white text-hero hover:bg-primary-tint" : "bg-primary text-white hover:bg-primary-hover"
              }`}
            >
              See what happened today <ArrowRight size={18} aria-hidden="true" />
            </Link>
          )}
          <span className={`inline-flex min-h-9 items-center gap-1.5 rounded-full px-3 text-sm font-semibold ${look.chip}`}>
            <HandHeart size={15} aria-hidden="true" /> {open} open request{open === 1 ? "" : "s"}
          </span>
          <span className={`inline-flex min-h-9 items-center gap-1.5 rounded-full px-3 text-sm font-semibold ${look.chip}`}>
            <Users size={15} aria-hidden="true" /> {reachable} of {o.care_circle.length} helpers reachable
          </span>
        </div>
      </div>
    </section>
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

  return (
    <div className="flex flex-col gap-8">
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

      <section className="flex flex-col gap-4" aria-labelledby="health-snap">
        <SectionTitle
          action={
            o.health && (
              <span className="flex items-center gap-3">
                {o.health.data_mode !== "live" && <SimulatedPill />}
                <Link href="/health" className="inline-flex items-center gap-1 text-sm font-semibold text-primary hover:underline">
                  Health trends <ArrowRight size={15} aria-hidden="true" />
                </Link>
              </span>
            )
          }
        >
          <span id="health-snap">{name}&apos;s week vs usual</span>
        </SectionTitle>
        {o.health ? (
          <ul className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {o.health.trends.map((t) => (
              <TrendTile key={t.metric} trend={t} obs={o.health!.observations} />
            ))}
          </ul>
        ) : (
          <EmptyBlock title={`${name} hasn't shared health data`} icon={HeartPulse}>
            That&apos;s {name}&apos;s choice. Sharing can be turned on by texting FarCare.
          </EmptyBlock>
        )}
      </section>

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <section className="flex min-w-0 flex-col gap-4" aria-labelledby="req-title">
          <SectionTitle
            action={
              <Link href="/requests" className="inline-flex items-center gap-1 text-sm font-semibold text-primary hover:underline">
                All requests <ArrowRight size={15} aria-hidden="true" />
              </Link>
            }
          >
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

        <div className="flex min-w-0 flex-col gap-8">
          <section className="flex flex-col gap-4" aria-labelledby="chk-title">
            <SectionTitle>
              <span id="chk-title">Latest check-in</span>
            </SectionTitle>
            <CheckInCard checkin={o.latest_checkin} shared={o.permissions.share_checkins ?? false} name={name} />
          </section>

          <section className="flex flex-col gap-4" aria-labelledby="act-title">
            <SectionTitle
              action={
                <Link href="/activity" className="inline-flex items-center gap-1 text-sm font-semibold text-primary hover:underline">
                  Everything <ArrowRight size={15} aria-hidden="true" />
                </Link>
              }
            >
              <span id="act-title">Recent activity</span>
            </SectionTitle>
            <div className="rounded-2xl border border-border bg-surface p-5">
              {recent.length ? <Feed events={recent} /> : <p className="text-body">Nothing yet today.</p>}
            </div>
          </section>
        </div>
      </div>

      <section className="flex flex-col gap-4" aria-labelledby="circle-title">
        <SectionTitle
          action={
            <Link href="/circle" className="inline-flex items-center gap-1 text-sm font-semibold text-primary hover:underline">
              Care Circle <ArrowRight size={15} aria-hidden="true" />
            </Link>
          }
        >
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

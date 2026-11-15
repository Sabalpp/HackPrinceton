"use client";

import { ShieldCheck } from "lucide-react";
import { api, type ParentOverview, type SafetyIncident } from "@/lib/api";
import { useLiveQuery } from "@/lib/dashboard";
import { Ready } from "../shell";
import { EmptyBlock, ErrorBlock, LoadingBlock, PageHeader, SectionTitle } from "../kit";
import { IncidentPanel } from "../incident";

const fetchIncidents = (token: string, parentId: string) => api.incidents(token, parentId, false);
const fetchRequests = (token: string, parentId: string) => api.careRequests(token, parentId, 200);

export function SafetyScreen() {
  return <Ready>{(r) => <Safety o={r.overview} />}</Ready>;
}

function Safety({ o }: { o: ParentOverview }) {
  const name = o.parent.display_name;
  const incidents = useLiveQuery(fetchIncidents);
  const requests = useLiveQuery(fetchRequests);
  const relOf = (id: string | null) => o.care_circle.find((m) => m.responder_id === id)?.relationship;
  const all = incidents.data ?? [];
  const active = all.filter((i) => i.status !== "resolved");
  const past = all.filter((i) => i.status === "resolved");
  const linked = (i: SafetyIncident) => (requests.data ?? []).filter((r) => r.incident_id === i.incident_id);

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        eyebrow="Safety alerts"
        title="Emergency center"
        description={`Possible falls and worrying messages. FarCare checks with ${name} first, then alerts the family and the Care Circle if there's no reply. It never calls emergency services itself.`}
      />

      {incidents.loading && !incidents.data && <LoadingBlock label="Loading alerts" rows={2} />}
      {incidents.error && !incidents.data && <ErrorBlock title="Couldn't load alerts" message={incidents.error} />}

      {incidents.data && (
        <>
          <section className="flex flex-col gap-4" aria-labelledby="active-title">
            <SectionTitle>
              <span id="active-title">Active now</span>
            </SectionTitle>
            {active.length ? (
              active.map((i) => <IncidentPanel key={i.incident_id} incident={i} parent={o.parent} requests={linked(i)} relationshipOf={relOf} />)
            ) : (
              <EmptyBlock title="No active alerts" icon={ShieldCheck}>
                Everything is calm. If a possible fall is detected or {name} asks for help, it appears here instantly.
              </EmptyBlock>
            )}
          </section>

          <section className="flex flex-col gap-4" aria-labelledby="past-title">
            <SectionTitle>
              <span id="past-title">Past alerts ({past.length})</span>
            </SectionTitle>
            {past.length ? (
              <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                {past.map((i) => (
                  <IncidentPanel key={i.incident_id} incident={i} parent={o.parent} requests={linked(i)} relationshipOf={relOf} />
                ))}
              </div>
            ) : (
              <p className="text-body">No past alerts.</p>
            )}
          </section>
        </>
      )}
    </div>
  );
}

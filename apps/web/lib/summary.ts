/**
 * Plain-language sentences derived from the overview. Pure functions, so every panel says the
 * same thing the same way, and the "Right now" summary agrees with the cards below it.
 */
import type { CareRequest, ParentOverview, SafetyIncident } from "./api";
import { CATEGORY_LABEL, isRequestStopped, type Tone } from "./format";

const whoOf = (r: CareRequest) => r.responder_name ?? "A helper";

/** One sentence for where a care request stands. */
export function requestSentence(r: CareRequest, parentName: string): string {
  const who = whoOf(r);
  switch (r.status) {
    case "created":
    case "awaiting_approval":
      return `Waiting for ${parentName} to say yes`;
    case "approved":
    case "sent":
      return r.responder_name ? `Asked ${who}, waiting for a reply` : "Finding someone to help";
    case "accepted": {
      const extra = r.estimated_arrival_minutes
        ? ` · arriving in about ${r.estimated_arrival_minutes} min`
        : r.responder_note
          ? ` · “${r.responder_note}”`
          : "";
      return `${who} will help${extra}`;
    }
    case "completed":
      return `${who} helped`;
    case "declined":
      return `${who} can't make it`;
    case "expired":
      return "No one replied in time";
    case "cancelled":
      return "Cancelled";
    case "failed":
      return "The message couldn't be sent";
  }
}

export const isRequestOpen = (r: CareRequest) => r.status !== "completed" && !isRequestStopped(r.status);

/** Open requests first, then most recently updated. */
export function sortRequests(requests: CareRequest[]): CareRequest[] {
  return [...requests].sort((a, b) => {
    const open = Number(isRequestOpen(b)) - Number(isRequestOpen(a));
    return open !== 0 ? open : b.updated_at.localeCompare(a.updated_at);
  });
}

/** The single most important fact about an incident, as a headline. */
export function incidentNow(
  incident: SafetyIncident,
  request: CareRequest | undefined,
  name: string,
): { title: string; tone: Tone } {
  if (incident.no_responder_available)
    return { title: "No one nearby can go right now", tone: "urgent" };
  if (incident.status === "checking") return { title: `Checking on ${name}`, tone: "waiting" };
  if (request?.status === "accepted") {
    const eta = request.estimated_arrival_minutes;
    return { title: `${whoOf(request)} is on the way${eta ? ` · about ${eta} min` : ""}`, tone: "coming" };
  }
  if (request && (request.status === "sent" || request.status === "approved"))
    return { title: `Asking ${whoOf(request)} to go over`, tone: "urgent" };
  return { title: `${name} needs someone to check in`, tone: "urgent" };
}

export type Summary = { tone: Tone; title: string; detail: string | null };

/** The "Right now" line at the top of the dashboard. */
export function summarize(o: ParentOverview): Summary {
  const name = o.parent.display_name;
  const incident = o.active_incidents[0];
  if (incident) {
    const req = o.recent_requests.find((r) => r.incident_id === incident.incident_id);
    const now = incidentNow(incident, req, name);
    return { tone: now.tone, title: now.title, detail: "Safety alert in progress. Details below." };
  }

  const open = sortRequests(o.recent_requests).filter(isRequestOpen);
  const needsApproval = open.find((r) => r.status === "awaiting_approval" || r.status === "created");
  if (needsApproval) {
    return {
      tone: "waiting",
      title: `Waiting for ${name} to approve a request`,
      detail: `${CATEGORY_LABEL[needsApproval.request_type]}. Nobody is contacted until ${name} says yes.`,
    };
  }

  const notable = o.health?.trends.filter((t) => t.notable) ?? [];
  if (notable.length > 0) {
    const t = notable[0];
    const what =
      t.metric === "steps"
        ? `${name} has been walking less than usual`
        : t.metric === "sleep_minutes"
          ? `${name} has been sleeping ${t.direction === "up" ? "more" : "less"} than usual`
          : `${name}'s resting heart rate is ${t.direction === "up" ? "higher" : "lower"} than usual`;
    return {
      tone: "waiting",
      title: what,
      detail: `Compared with ${name}'s own last few weeks. A good moment to call and check in.`,
    };
  }

  const next = open[0];
  return {
    tone: "done",
    title: "No alerts. Nothing needs you right now.",
    detail: next
      ? `${requestSentence(next, name)} (${CATEGORY_LABEL[next.request_type].toLowerCase()}${
          next.requested_time ? `, ${next.requested_time}` : ""
        }).`
      : "No open help requests.",
  };
}

/** Plain-language labels and time formatting. No medical or diagnostic wording here. */
import type { AssistanceCategory, CareRequestStatus, IncidentStatus, SafetyIncident } from "./api";

export const CATEGORY_LABEL: Record<AssistanceCategory, string> = {
  grocery_assistance: "Groceries",
  transportation: "Rides",
  meal_assistance: "Meals",
  medication_pickup: "Pharmacy pickup",
  household_help: "Around the house",
  companionship: "Company",
  urgent_check_in: "Urgent check-in",
  other: "Other help",
};

/** Visual tone of a status chip. Maps to the brand meaning of colour in globals.css. */
export type Tone = "waiting" | "coming" | "urgent" | "done" | "neutral";

export const REQUEST_STATUS: Record<CareRequestStatus, { label: string; tone: Tone }> = {
  created: { label: "New", tone: "waiting" },
  awaiting_approval: { label: "Waiting for OK", tone: "waiting" },
  approved: { label: "Approved", tone: "waiting" },
  sent: { label: "Waiting for reply", tone: "waiting" },
  accepted: { label: "Accepted", tone: "coming" },
  completed: { label: "Done", tone: "done" },
  declined: { label: "Declined", tone: "neutral" },
  cancelled: { label: "Cancelled", tone: "neutral" },
  expired: { label: "No reply in time", tone: "neutral" },
  failed: { label: "Couldn't send", tone: "urgent" },
};

export const INCIDENT_STATUS: Record<IncidentStatus, { label: string; tone: Tone }> = {
  checking: { label: "Checking in", tone: "waiting" },
  escalated: { label: "Escalated", tone: "urgent" },
  responder_en_route: { label: "Help on the way", tone: "coming" },
  resolved: { label: "Resolved", tone: "done" },
};

export function incidentHeadline(incident: SafetyIncident, name: string): string {
  return incident.trigger === "fall.suspected"
    ? "Possible fall"
    : `${name} sent a message that needs attention`;
}

export function escalationText(incident: SafetyIncident, name: string): string | null {
  switch (incident.escalation_reason) {
    case "help_requested":
      return `${name} replied asking for help.`;
    case "no_response":
      return `${name} didn't reply to the safety check in time.`;
    case "parent_reported_emergency":
      return `${name} said this is an emergency.`;
    default:
      return null;
  }
}

/** Stepper position for a care request: 0 asked, 1 sent, 2 accepted, 3 done. */
export function requestStep(status: CareRequestStatus): number {
  switch (status) {
    case "created":
    case "awaiting_approval":
      return 0;
    case "approved":
    case "sent":
    case "declined":
    case "expired":
      return 1;
    case "accepted":
      return 2;
    case "completed":
      return 3;
    case "cancelled":
    case "failed":
      return 0;
  }
}

export const isRequestStopped = (s: CareRequestStatus) =>
  s === "declined" || s === "cancelled" || s === "expired" || s === "failed";

export function relationshipLabel(rel: string): string {
  return rel.charAt(0).toUpperCase() + rel.slice(1);
}

/** "9:42 PM" in a given IANA time zone (undefined = the viewer's own). */
export function clockTime(at: Date | string | number, timeZone?: string): string {
  return new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone }).format(
    new Date(at),
  );
}

/** "Sat" style weekday, only shown when the parent's day differs from the viewer's. */
export function weekday(at: Date | number, timeZone?: string): string {
  return new Intl.DateTimeFormat("en-US", { weekday: "short", timeZone }).format(new Date(at));
}

/** Short city name from an IANA zone: "Asia/Kathmandu" -> "Kathmandu". */
export function zoneCity(timeZone: string): string {
  return timeZone.split("/").pop()?.replaceAll("_", " ") ?? timeZone;
}

export function relativeTime(iso: string, now: number): string {
  const diff = Math.round((now - new Date(iso).getTime()) / 1000);
  if (diff < 45) return "just now";
  const min = Math.round(diff / 60);
  if (min < 60) return `${min} min ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr} hr ago`;
  const days = Math.round(hr / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

export function countdown(iso: string, now: number): string | null {
  const ms = new Date(iso).getTime() - now;
  if (ms <= 0) return null;
  const s = Math.ceil(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

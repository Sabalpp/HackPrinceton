/**
 * Dummy data for NEXT_PUBLIC_USE_MOCKS=true. Every object is typed against the GENERATED
 * contracts (lib/api-types.ts), so `npm run typecheck` fails if the backend shape drifts.
 * All people are synthetic and all sensor/health data is labeled simulated.
 *
 * `seed()` builds a fresh copy of the demo world: Sita (parent, Kathmandu), Aarav (child,
 * viewer), Asha (neighbor) and Ram (family friend), with a few days of history.
 */
import type { components } from "./api-types";

type S = components["schemas"];

export const PARENT_ID = "parent_001";

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

export type MockPerson = {
  person_id: string;
  display_name: string;
  role: S["PersonRole"];
  timezone: string;
  has_handle: boolean;
  reachable: boolean;
};

export type MockOutbound = {
  to: string | null;
  to_role: string | null;
  channel: string;
  status: string;
  text: string;
  created_at: string;
};

export type MockDb = {
  now: () => number;
  people: MockPerson[];
  parent: S["ParentProfile"];
  permissions: { share_health: boolean; share_checkins: boolean };
  circle: S["CareCircleMember"][];
  requests: S["CareRequest"][];
  incidents: S["SafetyIncident"][];
  checkins: S["CheckIn"][];
  observations: S["HealthObservation"][];
  timeline: S["TimelineEvent"][];
  outbox: MockOutbound[];
  nextEventId: number;
};

const iso = (ms: number) => new Date(ms).toISOString();

/** 35 days of simulated daily data with a gentle decline over the last 5 days (demo story). */
function buildObservations(now: number): S["HealthObservation"][] {
  const today = new Date(now);
  const startOfDay = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
  return Array.from({ length: 35 }, (_, i) => {
    const offset = 34 - i;
    const decline = offset < 5 ? (5 - offset) / 5 : 0;
    const wobble = ((i * 337) % 600) - 300;
    return {
      observation_id: `sim_${PARENT_ID}_${i}`,
      parent_id: PARENT_ID,
      source: "simulated",
      data_mode: "simulated",
      timestamp: iso(startOfDay - offset * DAY),
      steps: Math.round((4000 + wobble) * (1 - 0.6 * decline)),
      resting_heart_rate: Math.round(67 + ((i * 7) % 3) - 1 + 5 * decline),
      sleep_minutes: Math.round(420 + ((i * 53) % 40) - 20 - 55 * decline),
    };
  });
}

export function seed(nowFn: () => number = () => Date.now()): MockDb {
  const now = nowFn();
  const t = (msAgo: number) => iso(now - msAgo);

  const circle: S["CareCircleMember"][] = [
    {
      responder_id: "responder_001",
      display_name: "Asha",
      relationship: "neighbor",
      categories: ["grocery_assistance", "household_help", "companionship", "meal_assistance", "medication_pickup"],
      emergency_contact: true,
      priority: 10,
      reachable: true,
    },
    {
      responder_id: "responder_002",
      display_name: "Ram",
      relationship: "family friend",
      categories: ["transportation", "grocery_assistance", "medication_pickup"],
      emergency_contact: true,
      priority: 20,
      reachable: true,
    },
  ];

  const requests: S["CareRequest"][] = [
    {
      request_id: "care_003",
      parent_id: PARENT_ID,
      request_type: "grocery_assistance",
      status: "accepted",
      responder_id: "responder_001",
      responder_name: "Asha",
      parent_approved: true,
      approval_basis: "parent_reply",
      origin: "conversation",
      details: "Rice, lentils and vegetables",
      requested_time: "tomorrow",
      estimated_arrival_minutes: null,
      responder_note: "4 PM",
      incident_id: null,
      created_at: t(2 * HOUR + 10 * MIN),
      updated_at: t(2 * HOUR),
    },
    {
      request_id: "care_002",
      parent_id: PARENT_ID,
      request_type: "medication_pickup",
      status: "completed",
      responder_id: "responder_002",
      responder_name: "Ram",
      parent_approved: true,
      approval_basis: "parent_reply",
      origin: "conversation",
      details: "",
      requested_time: "this evening",
      estimated_arrival_minutes: null,
      responder_note: "6 PM",
      incident_id: null,
      created_at: t(2 * DAY + 3 * HOUR),
      updated_at: t(2 * DAY),
    },
    {
      request_id: "care_001",
      parent_id: PARENT_ID,
      request_type: "transportation",
      status: "declined",
      responder_id: "responder_002",
      responder_name: "Ram",
      parent_approved: true,
      approval_basis: "parent_reply",
      origin: "conversation",
      details: "Temple on Saturday morning",
      requested_time: "Saturday 8 AM",
      estimated_arrival_minutes: null,
      responder_note: null,
      incident_id: null,
      created_at: t(4 * DAY + 2 * HOUR),
      updated_at: t(4 * DAY + HOUR),
    },
  ];

  const incidents: S["SafetyIncident"][] = [
    {
      incident_id: "inc_001",
      parent_id: PARENT_ID,
      trigger: "fall.suspected",
      source: "simulated_imu",
      data_mode: "simulated",
      confidence: 0.81,
      status: "resolved",
      escalation_reason: null,
      check_deadline: t(3 * DAY - 45_000),
      escalated_at: null,
      no_responder_available: false,
      opened_at: t(3 * DAY),
      resolution: "parent_ok",
      resolved_at: t(3 * DAY - 30_000),
    },
  ];

  const checkins: S["CheckIn"][] = [
    {
      checkin_id: "chk_002",
      parent_id: PARENT_ID,
      summary: "I haven't been feeling very energetic lately.",
      mood: "low",
      energy: "low",
      created_at: t(3 * HOUR),
    },
  ];

  // Newest first, like GET /timeline.
  const events: Omit<S["TimelineEvent"], "id" | "parent_id">[] = [
    { kind: "care_request.updated", summary: "Asha accepted at 4 PM", ref_id: "care_003", data: { from: "sent", to: "accepted", request_type: "grocery_assistance" }, created_at: t(2 * HOUR) },
    { kind: "care_request.updated", summary: "Sita said yes. Asked Asha for help.", ref_id: "care_003", data: { from: "awaiting_approval", to: "sent" }, created_at: t(2 * HOUR + 5 * MIN) },
    { kind: "care_request.created", summary: "Sita asked for help with groceries (tomorrow)", ref_id: "care_003", data: { request_type: "grocery_assistance" }, created_at: t(2 * HOUR + 10 * MIN) },
    { kind: "checkin.recorded", summary: "Sita checked in: low energy, low mood", ref_id: "chk_002", data: {}, created_at: t(3 * HOUR) },
    { kind: "care_request.updated", summary: "Ram finished the pharmacy pickup", ref_id: "care_002", data: { from: "accepted", to: "completed" }, created_at: t(2 * DAY) },
    { kind: "care_request.updated", summary: "Ram accepted at 6 PM", ref_id: "care_002", data: { from: "sent", to: "accepted" }, created_at: t(2 * DAY + 2 * HOUR) },
    { kind: "care_request.created", summary: "Sita asked for a pharmacy pickup (this evening)", ref_id: "care_002", data: { request_type: "medication_pickup" }, created_at: t(2 * DAY + 3 * HOUR) },
    { kind: "incident.resolved", summary: "Sita replied OK. Alert closed, nobody else was contacted.", ref_id: "inc_001", data: { resolution: "parent_ok" }, created_at: t(3 * DAY - 30_000) },
    { kind: "incident.opened", summary: "Possible fall detected (simulated, detector score 0.81). Safety check sent to Sita.", ref_id: "inc_001", data: { confidence: 0.81 }, created_at: t(3 * DAY) },
    { kind: "care_request.updated", summary: "Ram can't make it on Saturday", ref_id: "care_001", data: { from: "sent", to: "declined" }, created_at: t(4 * DAY + HOUR) },
    { kind: "care_request.created", summary: "Sita asked for a ride to the temple (Saturday 8 AM)", ref_id: "care_001", data: { request_type: "transportation" }, created_at: t(4 * DAY + 2 * HOUR) },
  ];
  const timeline: S["TimelineEvent"][] = events.map((e, i) => ({
    ...e,
    id: events.length - i,
    parent_id: PARENT_ID,
  }));

  return {
    now: nowFn,
    people: [
      { person_id: PARENT_ID, display_name: "Sita", role: "parent", timezone: "Asia/Kathmandu", has_handle: true, reachable: true },
      { person_id: "child_001", display_name: "Aarav", role: "child", timezone: "America/New_York", has_handle: true, reachable: true },
      { person_id: "responder_001", display_name: "Asha", role: "responder", timezone: "Asia/Kathmandu", has_handle: true, reachable: true },
      { person_id: "responder_002", display_name: "Ram", role: "responder", timezone: "Asia/Kathmandu", has_handle: true, reachable: true },
    ],
    parent: { parent_id: PARENT_ID, display_name: "Sita", timezone: "Asia/Kathmandu", emergency_number: "102" },
    permissions: { share_health: true, share_checkins: true },
    circle,
    requests,
    incidents,
    checkins,
    observations: buildObservations(now),
    timeline,
    outbox: [
      { to: "Aarav", to_role: "child", channel: "console", status: "sent", text: "Asha (neighbor) accepted Mom's grocery request for tomorrow at 4 PM.", created_at: t(2 * HOUR) },
      { to: "Sita", to_role: "parent", channel: "console", status: "sent", text: "Good news! Asha can help with groceries tomorrow at 4 PM.", created_at: t(2 * HOUR) },
    ],
    nextEventId: timeline.length + 1,
  };
}

export const mockSystemHealth: S["SystemHealth"] = {
  status: "ok",
  environment: "development",
  database: "ok",
  messaging: "console",
  photon_delivery: "off",
  ai: "fallback",
  demo_mode: true,
};

export const mockMe: S["Me"] = {
  person: { person_id: "child_001", display_name: "Aarav", role: "child", reachable: true },
  parent_ids: [PARENT_ID],
};

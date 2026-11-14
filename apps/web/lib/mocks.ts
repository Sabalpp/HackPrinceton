/**
 * Mock API responses for UI development (NEXT_PUBLIC_USE_MOCKS=true).
 * Typed against the generated contracts, so `npm run typecheck` fails if they drift.
 * All data is synthetic and labeled simulated.
 */
import type { components } from "./api-types";

type S = components["schemas"];

const day = (offset: number) => new Date(Date.UTC(2026, 10, 14 - offset)).toISOString();

const observations: S["HealthObservation"][] = Array.from({ length: 35 }, (_, i) => {
  const offset = 34 - i;
  const decline = offset < 5 ? (5 - offset) / 5 : 0;
  return {
    observation_id: `sim_parent_001_${i}`,
    parent_id: "parent_001",
    source: "simulated",
    data_mode: "simulated",
    timestamp: day(offset),
    steps: Math.round((4000 + ((i * 337) % 600) - 300) * (1 - 0.6 * decline)),
    resting_heart_rate: Math.round(67 + 5 * decline),
    sleep_minutes: Math.round(420 - 55 * decline),
  };
});

const overview: S["ParentOverview"] = {
  parent: {
    parent_id: "parent_001",
    display_name: "Sita",
    timezone: "Asia/Kathmandu",
    emergency_number: "102",
  },
  viewer_role: "child",
  permissions: { share_health: true, share_checkins: true },
  latest_checkin: {
    checkin_id: "chk_1",
    parent_id: "parent_001",
    summary: "I haven't been feeling very energetic lately.",
    mood: "low",
    energy: "low",
    created_at: "2026-11-14T15:00:00Z",
  },
  health: {
    parent_id: "parent_001",
    data_mode: "simulated",
    observations,
    trends: [
      {
        metric: "steps",
        baseline: 4012,
        recent: 1890,
        percent_change: -52.9,
        direction: "down",
        notable: true,
        observation: "Average daily steps over the last 5 days (1,890) are 53% lower than usual (4,012).",
      },
      {
        metric: "sleep_minutes",
        baseline: 419,
        recent: 386,
        percent_change: -7.9,
        direction: "down",
        notable: false,
        observation: "Average nightly sleep over the last 5 days (386 min) is 8% lower than usual (419 min).",
      },
      {
        metric: "resting_heart_rate",
        baseline: 67,
        recent: 70,
        percent_change: 4.5,
        direction: "stable",
        notable: false,
        observation: "Average resting heart rate over the last 5 days (70 bpm) is close to usual.",
      },
    ],
    suggest_checkin: true,
    disclaimer:
      "FarCare describes changes compared with this person's own recent history. It does not diagnose conditions or replace medical advice.",
  },
  active_incidents: [],
  recent_requests: [
    {
      request_id: "care_001",
      parent_id: "parent_001",
      request_type: "grocery_assistance",
      status: "accepted",
      responder_id: "responder_001",
      responder_name: "Asha",
      parent_approved: true,
      approval_basis: "parent_reply",
      origin: "conversation",
      details: "",
      requested_time: "tomorrow",
      estimated_arrival_minutes: null,
      responder_note: "4 PM",
      incident_id: null,
      created_at: "2026-11-14T15:10:00Z",
      updated_at: "2026-11-14T15:12:00Z",
    },
  ],
  care_circle: [
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
  ],
};

const timeline: S["TimelineEvent"][] = [
  {
    id: 3,
    parent_id: "parent_001",
    kind: "care_request.updated",
    summary: "Asha accepted at 4 PM",
    ref_id: "care_001",
    data: { from: "sent", to: "accepted" },
    created_at: "2026-11-14T15:12:00Z",
  },
  {
    id: 2,
    parent_id: "parent_001",
    kind: "care_request.updated",
    summary: "Asked Asha for help",
    ref_id: "care_001",
    data: { from: "approved", to: "sent" },
    created_at: "2026-11-14T15:11:00Z",
  },
  {
    id: 1,
    parent_id: "parent_001",
    kind: "care_request.created",
    summary: "Sita asked for help with groceries (tomorrow)",
    ref_id: "care_001",
    data: {},
    created_at: "2026-11-14T15:10:00Z",
  },
];

export const mockData = {
  health: {
    status: "ok",
    environment: "development",
    database: "ok",
    messaging: "console",
    photon_delivery: "off",
    ai: "fallback",
    demo_mode: true,
  } satisfies S["SystemHealth"],
  me: {
    person: { person_id: "child_001", display_name: "Aarav", role: "child", reachable: true },
    parent_ids: ["parent_001"],
  } satisfies S["Me"],
  overview,
  timeline,
};

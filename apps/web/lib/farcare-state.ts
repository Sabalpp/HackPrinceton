/**
 * PROPOSED contract for GET /api/state (the FarCare daily-call dashboard).
 *
 * This shape is NOT in packages/contracts yet. Per AGENTS.md ("Changing a shared contract"),
 * Sumit needs to add it to apps/api/app/schemas/contracts.py; once he does, delete these
 * hand-written types and re-export the generated ones from lib/api-types.ts instead.
 * mock/state.json is type-checked against this file (see use-farcare-state.ts).
 */
export type Level = "good" | "watch" | "urgent";
export type ThreadStatus = "new" | "improving" | "same" | "worse" | "fine";

export interface Parent {
  name: string;
  nickname: string;
  city: string;
  tz: string;
  /** "HH:MM", 24-hour, in the parent's time zone */
  call_time: string;
  emergency_number?: string;
}

export interface FamilyMember {
  name: string;
  role: "admin" | "member";
  city: string;
  tz: string;
  last_called: string | null;
}

export interface Helper {
  name: string;
  relationship: string;
  confirmed: boolean;
}

export interface HistoryEntry {
  day: string;
  source: "call" | "band" | "child";
  status?: ThreadStatus;
  note: string;
  quote_ne?: string;
  ask_en?: string;
  ask_ne?: string;
}

export interface Thread {
  id: number;
  topic: string;
  kind: "health" | "child_question" | "personal";
  status?: "open" | "resolved";
  last_status?: ThreadStatus;
  first_seen?: string;
  next_followup?: string;
  created_by?: string;
  flag_for_child?: boolean;
  history: HistoryEntry[];
}

export interface Vital {
  metric: "sleep_min" | "steps" | "resting_hr" | "spo2" | "bp" | string;
  label: string;
  value: number | string;
  normal: number | string;
  unit?: string;
  level: Level;
}

export interface TranscriptLine {
  role: "ai" | "ama";
  ne: string;
  en: string;
}

export interface AgendaItem {
  label: string;
  done?: boolean;
  current?: boolean;
}

export interface FarCareState {
  today: string;
  status: Level;
  status_headline: string;
  parent: Parent;
  family: FamilyMember[];
  helpers: Helper[];
  band?: { name: string; synced_min_ago: number };
  threads: Thread[];
  vitals: Vital[];
  meds_last7: boolean[];
  latest_call: {
    status: string;
    transcript: TranscriptLine[];
    learned?: { label: string; value: string }[];
  };
  agenda: AgendaItem[];
  digest: string;
  events: unknown[];
}

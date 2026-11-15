/**
 * In-browser stand-in for the FastAPI backend (NEXT_PUBLIC_USE_MOCKS=true only).
 *
 * Mirrors the real behaviour closely enough to rehearse the whole demo with no server:
 *  - health trends use the backend's rules (baseline = mean before the last 5 days, notable at
 *    steps ±25%, sleep ±12%, resting HR ±8%)
 *  - care requests: awaiting_approval -> (parent YES) sent -> (responder YES) accepted -> completed
 *  - incidents: checking -> (timeout or HELP) escalated -> (responder YES n) responder_en_route
 *    -> (responder DONE / parent OK / family) resolved
 *  - every change appends a TimelineEvent and is pushed to subscribers, like the SSE stream.
 * Replies are parsed with simple deterministic rules, like services/replies.py.
 */
import type { components } from "./api-types";
import type { DemoFallResult, DemoPerson, OutboxMessage } from "./api";
import { PARENT_ID, seed, type MockDb, type MockOutbound } from "./mocks";

type S = components["schemas"];

/** Shorter than production (45 s) so the no-reply path fits a rehearsal. */
const SAFETY_CHECK_SECONDS = 30;

let db: MockDb | null = null;
const listeners = new Set<(e: S["TimelineEvent"]) => void>();
const getDb = () => (db ??= seed());
const nowIso = () => new Date(getDb().now()).toISOString();
const clone = <T>(v: T): T => structuredClone(v);

/* ---------------- helpers ---------------- */

function emit(kind: string, summary: string, ref_id: string | null, data: Record<string, unknown> = {}) {
  const d = getDb();
  const ev: S["TimelineEvent"] = {
    id: d.nextEventId++,
    parent_id: PARENT_ID,
    kind,
    summary,
    ref_id,
    data,
    created_at: nowIso(),
  };
  d.timeline.unshift(ev);
  listeners.forEach((l) => l(clone(ev)));
}

function send(toName: string, text: string) {
  const d = getDb();
  const person = d.people.find((p) => p.display_name === toName);
  const msg: MockOutbound = {
    to: toName,
    to_role: person?.role ?? null,
    channel: "console",
    status: "sent",
    text,
    created_at: nowIso(),
  };
  d.outbox.unshift(msg);
  return msg;
}

const CATEGORY_WORDS: [RegExp, S["AssistanceCategory"], string][] = [
  [/grocer|vegetable|rice|shopping/i, "grocery_assistance", "groceries"],
  [/ride|drive|temple|hospital|clinic|doctor/i, "transportation", "a ride"],
  [/medicine|pharmacy|pill|tablet/i, "medication_pickup", "a pharmacy pickup"],
  [/cook|meal|food|lunch|dinner/i, "meal_assistance", "a meal"],
  [/fix|repair|clean|house/i, "household_help", "help around the house"],
  [/lonely|company|talk|visit/i, "companionship", "some company"],
];

function whenFrom(text: string): string | null {
  const m = text.match(/\b(today|tonight|tomorrow|this (?:morning|afternoon|evening)|(?:mon|tues|wednes|thurs|fri|satur|sun)day)\b/i);
  return m ? m[1].toLowerCase() : null;
}

const parent = () => getDb().parent;
const openIncident = () => getDb().incidents.find((i) => i.status !== "resolved");
const circleMember = (id: string | null) => getDb().circle.find((m) => m.responder_id === id);
const firstResponder = (category: S["AssistanceCategory"], skip: string[] = []) =>
  [...getDb().circle]
    .sort((a, b) => a.priority - b.priority)
    .find((m) => m.reachable && !skip.includes(m.responder_id) && (category === "urgent_check_in" ? m.emergency_contact : m.categories.includes(category)));

function touch(r: S["CareRequest"], patch: Partial<S["CareRequest"]>) {
  Object.assign(r, patch, { updated_at: nowIso() });
}

/* ---------------- incidents ---------------- */

const timers = new Map<string, ReturnType<typeof setTimeout>>();

function openFallIncident(score: number) {
  const d = getDb();
  const existing = openIncident();
  if (existing) {
    emit("fall.duplicate_suppressed", "Another possible-fall signal was merged into the open incident (no new alerts).", existing.incident_id);
    return existing;
  }
  const id = `inc_${Date.now().toString(36)}`;
  const inc: S["SafetyIncident"] = {
    incident_id: id,
    parent_id: PARENT_ID,
    trigger: "fall.suspected",
    source: "simulated_imu",
    data_mode: "simulated",
    confidence: score,
    status: "checking",
    escalation_reason: null,
    check_deadline: new Date(d.now() + SAFETY_CHECK_SECONDS * 1000).toISOString(),
    escalated_at: null,
    no_responder_available: false,
    opened_at: nowIso(),
    resolution: null,
    resolved_at: null,
  };
  d.incidents.unshift(inc);
  send(parent().display_name, "Are you okay? We noticed what might have been a fall. Reply OK or HELP.");
  emit("incident.opened", `Possible fall detected (simulated, detector score ${score.toFixed(2)}). Safety check sent to ${parent().display_name}.`, id, { confidence: score });
  timers.set(
    id,
    setTimeout(() => {
      if (inc.status === "checking") escalate(inc, "no_response");
    }, SAFETY_CHECK_SECONDS * 1000),
  );
  return inc;
}

function escalate(inc: S["SafetyIncident"], reason: S["EscalationReason"]) {
  const d = getDb();
  const name = parent().display_name;
  inc.status = "escalated";
  inc.escalation_reason = reason;
  inc.escalated_at = nowIso();
  const phrase =
    reason === "no_response" ? "didn't reply to the safety check" : reason === "help_requested" ? "replied asking for help" : "reported an emergency";
  const num = parent().emergency_number ? ` (${parent().emergency_number})` : "";
  send("Aarav", `URGENT: ${name} ${phrase}. If you think ${name} is in danger, call local emergency services${num}. We're asking ${name}'s Care Circle to check in.`);
  emit("incident.escalated", `${name} ${phrase}. Alerting family and Care Circle.`, inc.incident_id, { reason });

  const responder = firstResponder("urgent_check_in");
  if (!responder) {
    inc.no_responder_available = true;
    emit("incident.no_responder", "No Care Circle responder is available.", inc.incident_id);
    return;
  }
  const req: S["CareRequest"] = {
    request_id: `care_${Date.now().toString(36)}`,
    parent_id: PARENT_ID,
    request_type: "urgent_check_in",
    status: "sent",
    responder_id: responder.responder_id,
    responder_name: responder.display_name,
    parent_approved: false,
    approval_basis: "emergency_policy",
    origin: "emergency",
    details: "",
    requested_time: "now",
    estimated_arrival_minutes: null,
    responder_note: null,
    incident_id: inc.incident_id,
    created_at: nowIso(),
    updated_at: nowIso(),
  };
  d.requests.unshift(req);
  send(responder.display_name, `URGENT from FarCare: please check on ${name} now. Reply YES with minutes until you arrive (e.g. YES 5), or NO.`);
  emit("care_request.created", `Asked ${responder.display_name} to check on ${name} urgently`, req.request_id, { request_type: "urgent_check_in", incident_id: inc.incident_id });
}

function resolve(inc: S["SafetyIncident"], resolution: S["IncidentResolution"], text: string) {
  inc.status = "resolved";
  inc.resolution = resolution;
  inc.resolved_at = nowIso();
  clearTimeout(timers.get(inc.incident_id));
  for (const r of getDb().requests) {
    if (r.incident_id === inc.incident_id && (r.status === "sent" || r.status === "accepted")) {
      touch(r, { status: resolution === "responder_confirmed" ? "completed" : "cancelled" });
      if (resolution !== "responder_confirmed" && r.responder_name) send(r.responder_name, `${parent().display_name} is okay. No need to go over. Thank you!`);
    }
  }
  emit("incident.resolved", text, inc.incident_id, { resolution });
}

/* ---------------- inbound texts (POST /api/demo/inbound) ---------------- */

const YES = /^\s*(yes|y|ok(ay)?|sure|haan|ho)\b/i;
const NO = /^\s*(no|n|nope|sorry|can'?t|cannot)\b/i;

function fromParent(text: string) {
  const name = parent().display_name;
  const inc = openIncident();
  if (inc && inc.status === "checking") {
    if (/help|hurt|fell|emergency|bachau/i.test(text)) return escalate(inc, "help_requested");
    if (YES.test(text) || /fine|okay|ok\b/i.test(text)) {
      send("Aarav", `FYI: ${name} replied OK to a safety check. No action needed.`);
      return resolve(inc, "parent_ok", `${name} replied OK. Alert closed, nobody else was contacted.`);
    }
  }
  if (/emergency|can'?t breathe|chest pain|fell down/i.test(text)) {
    const d = getDb();
    const id = `inc_${Date.now().toString(36)}`;
    const msgInc: S["SafetyIncident"] = {
      incident_id: id, parent_id: PARENT_ID, trigger: "parent_message", source: "message", data_mode: "live",
      confidence: null, status: "checking", escalation_reason: null, check_deadline: null, escalated_at: null,
      no_responder_available: false, opened_at: nowIso(), resolution: null, resolved_at: null,
    };
    d.incidents.unshift(msgInc);
    emit("incident.opened", `${name} sent a message that may be an emergency.`, id);
    return escalate(msgInc, "parent_reported_emergency");
  }

  const pending = getDb().requests.find((r) => r.status === "awaiting_approval");
  if (pending && (YES.test(text) || NO.test(text))) {
    if (NO.test(text)) {
      touch(pending, { status: "cancelled" });
      send(name, "Okay, I won't ask anyone. Let me know if you change your mind.");
      return emit("care_request.updated", `${name} said no. Nobody was contacted.`, pending.request_id, { from: "awaiting_approval", to: "cancelled" });
    }
    touch(pending, { status: "sent", parent_approved: true, approval_basis: "parent_reply" });
    send(pending.responder_name ?? "Asha", `Hi ${pending.responder_name}, ${name} could use help with ${labelOf(pending)}${pending.requested_time ? ` (${pending.requested_time})` : ""}. Can you help? Reply YES (with a time) or NO.`);
    send(name, `Thanks! I've asked ${pending.responder_name}. I'll tell you when they reply.`);
    return emit("care_request.updated", `${name} said yes. Asked ${pending.responder_name} for help.`, pending.request_id, { from: "awaiting_approval", to: "sent" });
  }

  const cat = CATEGORY_WORDS.find(([re]) => re.test(text));
  if (cat && /help|can someone|need|could/i.test(text)) {
    const [, category, label] = cat;
    const responder = firstResponder(category);
    const when = whenFrom(text);
    const req: S["CareRequest"] = {
      request_id: `care_${Date.now().toString(36)}`,
      parent_id: PARENT_ID, request_type: category, status: responder ? "awaiting_approval" : "failed",
      responder_id: responder?.responder_id ?? null, responder_name: responder?.display_name ?? null,
      parent_approved: false, approval_basis: null, origin: "conversation", details: "",
      requested_time: when, estimated_arrival_minutes: null, responder_note: null, incident_id: null,
      created_at: nowIso(), updated_at: nowIso(),
    };
    getDb().requests.unshift(req);
    emit("care_request.created", `${name} asked for help with ${label}${when ? ` (${when})` : ""}`, req.request_id, { request_type: category });
    if (!responder) {
      send(name, "I'm sorry, nobody in your Care Circle can help with that right now. I've let Aarav know.");
      return;
    }
    send(name, `Would you like me to ask ${responder.display_name} (your ${responder.relationship}) to help with ${label}${when ? ` ${when}` : ""}? Reply YES or NO.`);
    return;
  }

  if (/tired|energ|sleep|lonely|sad|well|feel/i.test(text)) {
    const low = /tired|energ|sad|lonely|not/i.test(text);
    const chk: S["CheckIn"] = {
      checkin_id: `chk_${Date.now().toString(36)}`, parent_id: PARENT_ID, summary: text,
      mood: low ? "low" : "good", energy: low ? "low" : "normal", created_at: nowIso(),
    };
    getDb().checkins.unshift(chk);
    send(name, low ? "I'm sorry you're feeling low. Have you been sleeping okay? Would you like me to ask Asha to stop by for tea?" : "That's lovely to hear!");
    return emit("checkin.recorded", `${name} checked in: ${chk.energy} energy, ${chk.mood} mood`, chk.checkin_id);
  }

  send(name, "Thank you for your message. I'm here if you need anything.");
}

function fromResponder(personId: string, text: string) {
  const name = parent().display_name;
  const member = circleMember(personId);
  const who = member?.display_name ?? "Responder";
  const req = getDb().requests.find((r) => r.responder_id === personId && (r.status === "sent" || r.status === "accepted"));
  if (!req) {
    send(who, "Thanks! There's nothing waiting for you right now.");
    return;
  }
  const inc = req.incident_id ? getDb().incidents.find((i) => i.incident_id === req.incident_id) : undefined;

  if (/done|finished|she'?s ok|okay now|all good|completed/i.test(text) && req.status === "accepted") {
    if (inc && inc.status !== "resolved") {
      send("Aarav", `${who} is with ${name} and says ${name} is okay.`);
      return resolve(inc, "responder_confirmed", `${who} confirmed ${name} is okay. Alert closed.`);
    }
    touch(req, { status: "completed" });
    send(name, `${who} marked your ${labelOf(req)} request as done.`);
    return emit("care_request.updated", `${who} finished ${labelOf(req)}`, req.request_id, { from: "accepted", to: "completed" });
  }

  if (NO.test(text) && req.status === "sent") {
    touch(req, { status: "declined" });
    emit("care_request.updated", `${who} can't make it`, req.request_id, { from: "sent", to: "declined" });
    const next = firstResponder(req.request_type, [personId]);
    if (next && inc) {
      // Emergencies go straight to the next emergency contact.
      const again: S["CareRequest"] = { ...clone(req), request_id: `care_${Date.now().toString(36)}`, status: "sent", responder_id: next.responder_id, responder_name: next.display_name, created_at: nowIso(), updated_at: nowIso() };
      getDb().requests.unshift(again);
      send(next.display_name, `URGENT from FarCare: please check on ${name} now. Reply YES with minutes until you arrive, or NO.`);
      return emit("care_request.created", `Asked ${next.display_name} to check on ${name} urgently`, again.request_id, { incident_id: inc.incident_id });
    }
    if (next) send(name, `${who} can't make it. Would you like me to ask ${next.display_name} instead? Reply YES or NO.`);
    return;
  }

  if (YES.test(text) && req.status === "sent") {
    const minutes = Number(text.match(/\b(\d{1,3})\b/)?.[1]) || null;
    const clock = text.match(/\b(\d{1,2}(?::\d{2})?\s?(?:am|pm))\b/i)?.[1]?.toUpperCase() ?? null;
    touch(req, { status: "accepted", estimated_arrival_minutes: inc ? minutes : null, responder_note: clock });
    if (inc && inc.status !== "resolved") {
      inc.status = "responder_en_route";
      send("Aarav", `${who} is on the way to check on ${name}${minutes ? ` (about ${minutes} min)` : ""}.`);
      return emit("incident.responder_en_route", `${who} is on the way${minutes ? ` (ETA ~${minutes} min)` : ""}.`, inc.incident_id, { request_id: req.request_id });
    }
    send(name, `Good news! ${who} can help with ${labelOf(req)}${clock ? ` at ${clock}` : ""}.`);
    send("Aarav", `${who} (${member?.relationship ?? "Care Circle"}) accepted Mom's ${labelOf(req)} request${clock ? ` at ${clock}` : ""}.`);
    return emit("care_request.updated", `${who} accepted${clock ? ` at ${clock}` : ""}`, req.request_id, { from: "sent", to: "accepted" });
  }

  send(who, "Sorry, I didn't catch that. Reply YES (with a time) or NO.");
}

function labelOf(r: S["CareRequest"]) {
  return CATEGORY_WORDS.find(([, c]) => c === r.request_type)?.[2] ?? (r.request_type === "urgent_check_in" ? "an urgent check-in" : "help");
}

/* ---------------- health summary (mirrors health/trends.py) ---------------- */

const THRESHOLDS = { steps: 25, sleep_minutes: 12, resting_heart_rate: 8 } as const;
const LABEL = { steps: ["daily steps", ""], sleep_minutes: ["nightly sleep", " min"], resting_heart_rate: ["resting heart rate", " bpm"] } as const;

function trend(obs: S["HealthObservation"][], metric: keyof typeof THRESHOLDS): S["MetricTrend"] {
  const vals = obs.map((o) => o[metric]).filter((v): v is number => typeof v === "number");
  if (vals.length < 10) {
    return { metric, baseline: null, recent: null, percent_change: null, direction: "insufficient_data", notable: false, observation: "Not enough data yet to compare with the usual." };
  }
  const recentVals = vals.slice(-5);
  const baseVals = vals.slice(-35, -5).slice(-30);
  const mean = (a: number[]) => a.reduce((s, v) => s + v, 0) / a.length;
  const baseline = Math.round(mean(baseVals));
  const recent = Math.round(mean(recentVals));
  const pct = Math.round(((recent - baseline) / baseline) * 1000) / 10;
  const notable = Math.abs(pct) >= THRESHOLDS[metric];
  const direction = Math.abs(pct) < THRESHOLDS[metric] / 2 ? "stable" : pct > 0 ? "up" : "down";
  const [label, unit] = LABEL[metric];
  const fmt = (n: number) => `${n.toLocaleString("en-US")}${unit}`;
  const observation =
    direction === "stable"
      ? `Average ${label} over the last 5 days (${fmt(recent)}) is close to usual.`
      : `Average ${label} over the last 5 days (${fmt(recent)}) is ${Math.abs(Math.round(pct))}% ${pct < 0 ? "lower" : "higher"} than usual (${fmt(baseline)}).`;
  return { metric, baseline, recent, percent_change: pct, direction, notable, observation };
}

function healthSummary(days = 35): S["HealthSummary"] | null {
  const d = getDb();
  if (!d.permissions.share_health) return null;
  const obs = d.observations.slice(-days);
  const trends = (["steps", "sleep_minutes", "resting_heart_rate"] as const).map((m) => trend(d.observations, m));
  return {
    parent_id: PARENT_ID,
    data_mode: "simulated",
    observations: clone(obs),
    trends,
    suggest_checkin: trends.some((t) => t.notable),
    disclaimer:
      "FarCare describes changes compared with this person's own recent history. It does not diagnose conditions or replace medical advice.",
  };
}

/* ---------------- public "endpoints" ---------------- */

const delay = <T>(v: T, ms = 220) => new Promise<T>((r) => setTimeout(() => r(clone(v)), ms));

export const mockServer = {
  overview: (): Promise<S["ParentOverview"]> => {
    const d = getDb();
    return delay({
      parent: d.parent,
      viewer_role: "child",
      permissions: d.permissions,
      latest_checkin: d.permissions.share_checkins ? (d.checkins[0] ?? null) : null,
      health: healthSummary(),
      active_incidents: d.incidents.filter((i) => i.status !== "resolved"),
      recent_requests: d.requests.slice(0, 10),
      care_circle: d.circle,
    });
  },
  health: (days?: number) => delay(healthSummary(days)),
  careCircle: () => delay(getDb().circle),
  careRequests: (limit = 50) => delay(getDb().requests.slice(0, limit)),
  incidents: (activeOnly = false) => delay(getDb().incidents.filter((i) => !activeOnly || i.status !== "resolved")),
  timeline: (limit = 100) => delay(getDb().timeline.slice(0, limit)),
  resolveIncident: (incidentId: string) => {
    const inc = getDb().incidents.find((i) => i.incident_id === incidentId);
    if (!inc) return Promise.reject(new Error("Incident not found"));
    if (inc.status !== "resolved") resolve(inc, "family_resolved", `Aarav marked the alert resolved. Responders were told to stand down.`);
    return delay(inc);
  },
  demoFall: (trace: string) => {
    const isFall = trace === "fall";
    const score = isFall ? 0.87 : trace === "lying_down" ? 0.31 : 0.12;
    const inc = isFall ? openFallIncident(score) : null;
    const result: DemoFallResult = {
      trace,
      data_mode: "simulated",
      detection: { label: isFall ? "possible_fall" : "no_fall", score },
      ingest: inc ? { event_id: `simfall_${Date.now().toString(36)}`, duplicate: false, incident_id: inc.incident_id, detail: "incident opened" } : null,
    };
    return delay(result);
  },
  demoInbound: (personId: string, text: string) => {
    const before = getDb().outbox.length;
    const person = getDb().people.find((p) => p.person_id === personId);
    if (!person) return Promise.reject(new Error("Unknown person"));
    if (person.role === "parent") fromParent(text);
    else if (person.role === "responder") fromResponder(personId, text);
    else send(person.display_name, "Thanks! Updates about Mom will appear on your dashboard.");
    return delay({ outbound: getDb().outbox.slice(0, getDb().outbox.length - before).reverse() });
  },
  demoPeople: () =>
    delay<DemoPerson[]>(getDb().people.map((p) => ({ person_id: p.person_id, display_name: p.display_name, role: p.role, has_handle: p.has_handle, reachable: p.reachable }))),
  demoOutbox: (limit = 50) => delay<OutboxMessage[]>(getDb().outbox.slice(0, limit)),
  demoReset: () => {
    timers.forEach(clearTimeout);
    timers.clear();
    const fresh = seed();
    const d = getDb();
    Object.assign(d, { ...fresh, requests: [], incidents: [], checkins: [], timeline: [], outbox: [], nextEventId: d.nextEventId });
    emit("demo.reset", "Demo reset: workflow history cleared (people and health data kept).", null);
    return delay({ reset: true });
  },
  subscribe: (cb: (e: S["TimelineEvent"]) => void) => {
    listeners.add(cb);
    return () => listeners.delete(cb);
  },
};

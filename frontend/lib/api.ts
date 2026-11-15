/**
 * Typed FarCare API client: the ONLY place the UI talks to the backend.
 *
 * Types are GENERATED from the backend contracts (packages/contracts/openapi.json ->
 * lib/api-types.ts via `npm run gen:api`). Never hand-edit api-types.ts or redefine its shapes.
 *
 * NEXT_PUBLIC_USE_MOCKS=true  -> lib/mock-server.ts answers in the browser (dummy data, live events)
 * NEXT_PUBLIC_USE_MOCKS=false -> real FastAPI at NEXT_PUBLIC_API_URL, live updates over SSE
 */
import type { components } from "./api-types";
import { mockServer } from "./mock-server";
import { mockMe, mockSystemHealth } from "./mocks";

export type Schemas = components["schemas"];
export type ParentOverview = Schemas["ParentOverview"];
export type ParentProfile = Schemas["ParentProfile"];
export type HealthSummary = Schemas["HealthSummary"];
export type HealthObservation = Schemas["HealthObservation"];
export type MetricTrend = Schemas["MetricTrend"];
export type CareRequest = Schemas["CareRequest"];
export type CareRequestStatus = Schemas["CareRequestStatus"];
export type CareCircleMember = Schemas["CareCircleMember"];
export type AssistanceCategory = Schemas["AssistanceCategory"];
export type CheckIn = Schemas["CheckIn"];
export type SafetyIncident = Schemas["SafetyIncident"];
export type IncidentStatus = Schemas["IncidentStatus"];
export type TimelineEvent = Schemas["TimelineEvent"];
export type Me = Schemas["Me"];
export type SystemHealth = Schemas["SystemHealth"];

/*
 * /api/demo/* responses are plain dicts in the backend (untyped in OpenAPI). These mirror
 * backend/app/routes/demo.py exactly; ask Sumit to add response models if they change.
 */
export type DemoPerson = { person_id: string; display_name: string; role: string; has_handle: boolean; reachable: boolean };
export type OutboxMessage = { to: string | null; to_role: string | null; channel: string; status: string; text: string; created_at: string };
export type DemoFallResult = {
  trace: string;
  data_mode: "simulated";
  detection: { label: string; score: number; [k: string]: unknown };
  ingest: Schemas["FallIngestResult"] | null;
};
export type FallTrace = Schemas["FallDemoIn"]["trace"];

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "/backend";
export const USE_MOCKS = process.env.NEXT_PUBLIC_USE_MOCKS === "true";
let csrfToken: string | null = null;
export function setSessionCsrf(token: string | null) { csrfToken = token; }

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function request<T>(path: string, token: string | null, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: {
        ...(init?.body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init?.method && init.method !== "GET" && csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
      },
      cache: "no-store",
      // Explicit bearer credentials are development tooling only. Do not mix
      // them with the personal cookie identity, which the API must prioritize.
      credentials: token ? "omit" : "include",
    });
  } catch {
    // fetch() rejects only on network failure (API down, CORS, offline).
    throw new ApiError(0, "Can't reach FarCare right now.");
  }
  if (!res.ok) {
    if (res.status === 401 && !token && !path.startsWith("/api/auth/") && typeof window !== "undefined") {
      window.dispatchEvent(new Event("farcare:session-expired"));
    }
    let detail = "The request could not be completed. Please try again.";
    try {
      const body = await res.json() as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {}
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const get = <T>(path: string, token: string | null) => request<T>(path, token);
const post = <T>(path: string, token: string | null, body?: unknown) =>
  request<T>(path, token, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

const p = (parentId: string) => `/api/parents/${encodeURIComponent(parentId)}`;
const mock = <T>(fn: () => Promise<T>) => fn().catch((e: Error) => Promise.reject(new ApiError(404, e.message)));

export const api = {
  health: () => (USE_MOCKS ? Promise.resolve(mockSystemHealth) : get<SystemHealth>("/health", null)),
  me: (token: string) => (USE_MOCKS ? Promise.resolve(mockMe) : get<Me>("/api/me", token)),

  overview: (token: string, parentId: string) =>
    USE_MOCKS ? mockServer.overview() : get<ParentOverview>(`${p(parentId)}/overview`, token),
  healthSummary: (token: string, parentId: string, days = 35) =>
    USE_MOCKS ? mockServer.health(days) : get<HealthSummary | null>(`${p(parentId)}/health?days=${days}`, token),
  careCircle: (token: string, parentId: string) =>
    USE_MOCKS ? mockServer.careCircle() : get<CareCircleMember[]>(`${p(parentId)}/care-circle`, token),
  careRequests: (token: string, parentId: string, limit = 50) =>
    USE_MOCKS ? mockServer.careRequests(limit) : get<CareRequest[]>(`${p(parentId)}/care-requests?limit=${limit}`, token),
  incidents: (token: string, parentId: string, activeOnly = false) =>
    USE_MOCKS
      ? mockServer.incidents(activeOnly)
      : get<SafetyIncident[]>(`${p(parentId)}/incidents?active_only=${activeOnly}`, token),
  timeline: (token: string, parentId: string, limit = 100) =>
    USE_MOCKS ? mockServer.timeline(limit) : get<TimelineEvent[]>(`${p(parentId)}/timeline?limit=${limit}`, token),
  resolveIncident: (token: string, parentId: string, incidentId: string) =>
    USE_MOCKS
      ? mock(() => mockServer.resolveIncident(incidentId))
      : post<SafetyIncident>(`${p(parentId)}/incidents/${encodeURIComponent(incidentId)}/resolve`, token),

  // Demo controls: admin token + DEMO_MODE=true on the backend. Everything is labeled simulated.
  demoFall: (adminToken: string, parentId: string, trace: FallTrace = "fall") =>
    USE_MOCKS ? mockServer.demoFall(trace) : post<DemoFallResult>("/api/demo/fall", adminToken, { parent_id: parentId, trace }),
  demoInbound: (adminToken: string, personId: string, text: string) =>
    USE_MOCKS
      ? mock(() => mockServer.demoInbound(personId, text))
      : post<{ outbound: OutboxMessage[] }>("/api/demo/inbound", adminToken, { person_id: personId, text }),
  demoPeople: (adminToken: string) => (USE_MOCKS ? mockServer.demoPeople() : get<DemoPerson[]>("/api/demo/people", adminToken)),
  demoOutbox: (adminToken: string, limit = 50) =>
    USE_MOCKS ? mockServer.demoOutbox(limit) : get<OutboxMessage[]>(`/api/demo/outbox?limit=${limit}`, adminToken),
  demoReset: (adminToken: string) => (USE_MOCKS ? mockServer.demoReset() : post<{ reset: boolean }>("/api/demo/reset", adminToken)),
};

export type StreamStatus = "connecting" | "live" | "reconnecting";

/**
 * Live dashboard updates. Real mode: SSE (`event: timeline`), EventSource reconnects and resumes
 * via Last-Event-ID. Mock mode: events from the in-browser mock backend. Returns unsubscribe.
 */
export function subscribeTimeline(
  token: string,
  parentId: string,
  onEvent: (event: TimelineEvent) => void,
  onStatus?: (status: StreamStatus) => void,
): () => void {
  if (USE_MOCKS) {
    const off = mockServer.subscribe(onEvent);
    return () => {
      off();
    };
  }
  const url = `${API_URL}${p(parentId)}/stream${token ? `?access_token=${encodeURIComponent(token)}` : ""}`;
  const source = new EventSource(url, { withCredentials: true });
  source.onopen = () => onStatus?.("live");
  source.onerror = () => onStatus?.("reconnecting");
  source.addEventListener("timeline", (e) => onEvent(JSON.parse((e as MessageEvent).data)));
  return () => source.close();
}

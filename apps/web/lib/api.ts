/**
 * Typed FarCare API client. Types are GENERATED from the backend contracts
 * (packages/contracts/openapi.json -> lib/api-types.ts via `npm run gen:api`).
 * Never hand-edit api-types.ts; never redefine these shapes by hand.
 *
 * Set NEXT_PUBLIC_USE_MOCKS=true to develop the UI without a running backend.
 */
import type { components } from "./api-types";
import { mockData } from "./mocks";

export type Schemas = components["schemas"];
export type ParentOverview = Schemas["ParentOverview"];
export type HealthSummary = Schemas["HealthSummary"];
export type CareRequest = Schemas["CareRequest"];
export type CareCircleMember = Schemas["CareCircleMember"];
export type SafetyIncident = Schemas["SafetyIncident"];
export type TimelineEvent = Schemas["TimelineEvent"];
export type Me = Schemas["Me"];
export type SystemHealth = Schemas["SystemHealth"];

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
export const USE_MOCKS = process.env.NEXT_PUBLIC_USE_MOCKS === "true";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function get<T>(path: string, token: string | null): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    cache: "no-store",
  });
  if (!res.ok) throw new ApiError(res.status, `${res.status} ${res.statusText} for ${path}`);
  return (await res.json()) as T;
}

async function post<T>(path: string, token: string | null, body?: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, `${res.status} ${res.statusText} for ${path}`);
  return (await res.json()) as T;
}

export const api = {
  health: () => (USE_MOCKS ? Promise.resolve(mockData.health) : get<SystemHealth>("/health", null)),
  me: (token: string) => (USE_MOCKS ? Promise.resolve(mockData.me) : get<Me>("/api/me", token)),
  overview: (token: string, parentId: string) =>
    USE_MOCKS
      ? Promise.resolve(mockData.overview)
      : get<ParentOverview>(`/api/parents/${parentId}/overview`, token),
  timeline: (token: string, parentId: string) =>
    USE_MOCKS
      ? Promise.resolve(mockData.timeline)
      : get<TimelineEvent[]>(`/api/parents/${parentId}/timeline`, token),
  careRequests: (token: string, parentId: string) =>
    USE_MOCKS
      ? Promise.resolve(mockData.overview.recent_requests)
      : get<CareRequest[]>(`/api/parents/${parentId}/care-requests`, token),
  incidents: (token: string, parentId: string) =>
    USE_MOCKS
      ? Promise.resolve(mockData.overview.active_incidents)
      : get<SafetyIncident[]>(`/api/parents/${parentId}/incidents`, token),
  resolveIncident: (token: string, parentId: string, incidentId: string) =>
    post<SafetyIncident>(`/api/parents/${parentId}/incidents/${incidentId}/resolve`, token),
  // Demo controls (admin token, DEMO_MODE=true on the backend)
  demoFall: (adminToken: string, parentId: string, trace = "fall") =>
    post<unknown>("/api/demo/fall", adminToken, { parent_id: parentId, trace }),
  demoInbound: (adminToken: string, personId: string, text: string) =>
    post<unknown>("/api/demo/inbound", adminToken, { person_id: personId, text }),
};

/**
 * Live dashboard updates over Server-Sent Events. Returns an unsubscribe function.
 * EventSource reconnects automatically and resumes via Last-Event-ID.
 */
export function subscribeTimeline(
  token: string,
  parentId: string,
  onEvent: (event: TimelineEvent) => void,
): () => void {
  if (USE_MOCKS) return () => {};
  const url = `${API_URL}/api/parents/${parentId}/stream?access_token=${encodeURIComponent(token)}`;
  const source = new EventSource(url);
  source.addEventListener("timeline", (e) => onEvent(JSON.parse((e as MessageEvent).data)));
  return () => source.close();
}

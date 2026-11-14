import { request, type Schemas } from "./api";

export type Session = Schemas["SessionInfo"];
export type Registration = Schemas["RegisterIn"];
export type InvitationPreview = Schemas["InvitationInfo"];
export type AgentStatus = Schemas["AgentStatus"];
export type ResponderMember = Schemas["ResponderMember"];
export type SharingMember = Schemas["SharingMember"];
export type ParentHome = Schemas["ParentHome"];
export type ResponderTask = Schemas["ResponderTask"];
export type ResponderTasks = Schemas["ResponderTasks"];
export type InviteIssued = Schemas["InviteIssued"];
export type MetricName = Schemas["ReferenceIn"]["metric"];
export type Symptom = NonNullable<Schemas["BPReadingIn"]["symptoms"]>[number];
export type BPReadingInput = Schemas["BPReadingIn"];
export type BPReadingInfo = Schemas["BPReadingInfo"];
export type PersonalMetric = Schemas["PersonalMetric"];
export type PersonalHealthReport = Schemas["PersonalHealthReport"];
const post = <T>(path: string, body?: unknown) => request<T>(path, null, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const experience = {
  session: () => request<Session>("/api/auth/session", null),
  login: (email: string, password: string) => post<Session>("/api/auth/login", { email, password }),
  register: (body: Registration) => post<Session>("/api/auth/register", body),
  logout: () => post<void>("/api/auth/logout"),
  demo: (role: "child" | "parent" | "responder") => post<Session>("/api/auth/demo", { role }),
  invitation: (token: string) => post<InvitationPreview>("/api/auth/invitation", { token }),
  parentHome: () => request<ParentHome>("/api/parent/home", null),
  checkIn: (action_id: string, response: "okay" | "help", note?: string) => post<ParentHome>("/api/parent/check-in", { action_id,response,note }),
  parentRequest: (body: Schemas["HelpIn"]) => post<Schemas["CareRequest"]>("/api/parent/requests", body),
  parentDecision: (id: string, action_id: string, decision: "approve" | "cancel") => post<Schemas["CareRequest"]>(`/api/parent/requests/${encodeURIComponent(id)}/decision`, { action_id,decision }),
  responderTasks: () => request<ResponderTasks>("/api/responder/tasks", null),
  responderReply: (id: string, body: Schemas["ResponderReplyIn"]) => post<ResponderTask>(`/api/responder/tasks/${encodeURIComponent(id)}/reply`, body),
  createParent: (body: Schemas["CreateParentIn"]) => post<InviteIssued>("/api/onboarding/parents", body),
  invite: (body: Schemas["CreateInviteIn"]) => post<InviteIssued>("/api/onboarding/invites", body),
  approveResponder: (id: string, active: boolean, emergency_contact: boolean) => post<{ approved:boolean }>(`/api/parent/responders/${encodeURIComponent(id)}/approve`, { active, emergency_contact }),
  sharing: (body: Schemas["SharingIn"]) => post<SharingMember>("/api/parent/sharing", body),
  bloodPressure: (body: BPReadingInput) => post<BPReadingInfo>("/api/parent/blood-pressure", body),
  personalHealth: (id: string) => request<PersonalHealthReport | null>(`/api/parents/${encodeURIComponent(id)}/personal-health`, null),
  reference: (id: string, body: Schemas["ReferenceIn"]) => post<Schemas["ReferenceInfo"]>(`/api/parents/${encodeURIComponent(id)}/reference`, body),
};

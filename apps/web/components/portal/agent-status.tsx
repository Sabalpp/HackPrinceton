import { Circle, Radio } from "lucide-react";
import type { AgentStatus } from "@/lib/experience-api";

export function AgentStatusLine({ agent }: { agent: AgentStatus }) {
  const ready = agent.status === "healthy" && agent.messaging_ready;
  return <aside className="flex items-start gap-3 rounded-2xl border border-border bg-inset p-4 text-sm text-body"><span className={ready ? "text-primary" : "text-muted"}>{ready ? <Radio size={20} aria-hidden="true" /> : <Circle size={17} aria-hidden="true" />}</span><div><p className="font-semibold text-ink">{agent.messaging === "console" ? "In-app updates; phone messages are simulated" : ready ? "Messaging is available" : "In-app updates; messaging needs attention"}</p><p className="mt-0.5">{agent.detail}</p>{agent.last_heartbeat_at && <p className="mt-1 text-xs text-muted">Last system update: {new Date(agent.last_heartbeat_at).toLocaleString()}</p>}</div></aside>;
}

import { Circle, Radio } from "lucide-react";
import type { AgentStatus } from "@/lib/experience-api";

export function AgentStatusLine({ agent }: { agent: AgentStatus }) {
  const ready = agent.status === "healthy" && agent.messaging_ready;
  const channel = agent.messaging_platform === "sms" ? "SMS" : "iMessage";
  const title = agent.messaging === "console"
    ? "In-app updates; phone messages are simulated"
    : agent.messaging_opted_out ? `${channel} messages are paused`
    : ready ? `${channel} messaging is available` : `In-app updates; ${channel} needs attention`;
  return <aside className="flex items-start gap-3 rounded-2xl border border-border bg-inset p-4 text-sm text-body">
    <span className={ready ? "text-primary" : "text-muted"}>{ready ? <Radio size={20} aria-hidden="true" /> : <Circle size={17} aria-hidden="true" />}</span>
    <div><p className="font-semibold text-ink">{title}</p><p className="mt-0.5">{agent.detail}</p>
      {agent.messaging === "photon" && agent.messaging_platform === "sms" && <p className="mt-2">Reply HELP to your FarCare text for instructions. STOP pauses texts; START asks to resume. You can still use this app.</p>}
      {agent.last_heartbeat_at && <p className="mt-1 text-xs text-muted">Last system update: {new Date(agent.last_heartbeat_at).toLocaleString()}</p>}
    </div>
  </aside>;
}

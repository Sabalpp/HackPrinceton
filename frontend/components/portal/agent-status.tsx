import { MessageCircle, MessageCircleOff } from "lucide-react";
import type { AgentStatus } from "@/lib/experience-api";

/** One plain sentence about phone messages. System internals stay out of people's screens. */
export function AgentStatusLine({ agent }: { agent: AgentStatus }) {
  const ready = agent.status === "healthy" && agent.messaging_ready;
  const sms = agent.messaging === "photon" && agent.messaging_platform === "sms";
  const text = agent.messaging === "console"
    ? "Phone texts are simulated in this demo. Everything works here in the app."
    : agent.messaging_opted_out ? "Phone texts are paused. You can still use FarCare here."
    : ready ? "You can also text FarCare from your phone. It's the same conversation."
    : "Phone texts aren't working right now. You can still use FarCare here.";
  const Icon = ready ? MessageCircle : MessageCircleOff;
  return <p className="flex items-start gap-2.5 text-sm text-muted">
    <Icon size={18} className={`mt-0.5 shrink-0 ${ready ? "text-primary" : ""}`} aria-hidden="true" />
    <span>{text}{sms && " Texts are automated. Reply HELP for help or STOP to pause."}</span>
  </p>;
}

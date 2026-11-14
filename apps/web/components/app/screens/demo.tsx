"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Activity, Inbox, KeyRound, MessageSquareText, Play, RotateCcw, Send, Sparkles } from "lucide-react";
import { api, USE_MOCKS, type DemoFallResult, type DemoPerson, type FallTrace, type OutboxMessage, type SystemHealth } from "@/lib/api";
import { useDashboard } from "@/lib/dashboard";
import { btn } from "@/components/farcare/ui";
import { TimeAgo } from "@/components/ui/time";
import { PageHeader } from "../kit";

type Step = { n: string; who: string; label: string; run: { kind: "text"; person: string; text: string } | { kind: "fall" } | { kind: "wait" } };

// The stage script from docs/demo-plan.md, one click per step.
const SCRIPT: Step[] = [
  { n: "1", who: "Sita", label: "I haven't been feeling very energetic lately.", run: { kind: "text", person: "parent_001", text: "I haven't been feeling very energetic lately." } },
  { n: "3", who: "Sita", label: "Can someone help me with groceries tomorrow?", run: { kind: "text", person: "parent_001", text: "Can someone help me with groceries tomorrow?" } },
  { n: "4", who: "Sita", label: "Yes", run: { kind: "text", person: "parent_001", text: "Yes" } },
  { n: "5", who: "Asha", label: "Yes, I can help at 4 PM", run: { kind: "text", person: "responder_001", text: "Yes, I can help at 4 PM" } },
  { n: "6", who: "Sensor", label: "Replay a possible fall (simulated)", run: { kind: "fall" } },
  { n: "7", who: "Sita", label: "Stays silent until the safety check times out", run: { kind: "wait" } },
  { n: "8", who: "Asha", label: "YES 5", run: { kind: "text", person: "responder_001", text: "YES 5" } },
  { n: "9", who: "Asha", label: "Done, she's okay", run: { kind: "text", person: "responder_001", text: "Done, she's okay" } },
];
const EXTRAS: Step[] = [
  { n: "+", who: "Sita · instead of staying silent", label: "HELP", run: { kind: "text", person: "parent_001", text: "HELP" } },
  { n: "+", who: "Sita · false alarm", label: "OK", run: { kind: "text", person: "parent_001", text: "OK" } },
  { n: "+", who: "Asha · declines, Ram is asked", label: "Sorry, I can't", run: { kind: "text", person: "responder_001", text: "Sorry, I can't" } },
];
const TRACES: FallTrace[] = ["fall", "walking", "sitting", "bending", "lying_down", "recovery"];

export function DemoScreen() {
  const { state, version, refresh } = useDashboard();
  const parentId = state.phase === "ready" ? state.parentId : "parent_001";
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [log, setLog] = useState<{ id: number; text: string; ok: boolean }[]>([]);
  const [people, setPeople] = useState<DemoPerson[]>([]);
  const [outbox, setOutbox] = useState<OutboxMessage[]>([]);
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [fall, setFall] = useState<DemoFallResult | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const canAct = USE_MOCKS || token.trim().length > 0;

  const note = (text: string, ok = true) => setLog((l) => [{ id: Date.now() + Math.random(), text, ok }, ...l].slice(0, 8));

  useEffect(() => {
    api.health().then(setHealth, () => setHealth(null));
  }, []);

  const loadAdminData = useCallback(() => {
    if (!canAct) return Promise.resolve();
    return Promise.all([api.demoPeople(token), api.demoOutbox(token, 30)]).then(
      ([p, o]) => {
        setPeople(p);
        setOutbox(o);
      },
      () => note("Couldn't load demo data. Check the admin token and that DEMO_MODE=true.", false),
    );
  }, [canAct, token]);

  useEffect(() => {
    void loadAdminData();
  }, [loadAdminData, version]);

  async function act(key: string, label: string, fn: () => Promise<unknown>) {
    setBusy(key);
    try {
      await fn();
      note(label);
      refresh();
      await loadAdminData();
    } catch {
      note(`Failed: ${label}`, false);
    } finally {
      setBusy(null);
    }
  }

  function runStep(s: Step, key: string) {
    if (s.run.kind === "wait") {
      note("Waiting: the safety check escalates on its own when the countdown ends.");
      return;
    }
    if (s.run.kind === "fall") {
      return act(key, "Replayed a simulated fall trace", async () => setFall(await api.demoFall(token, parentId, "fall")));
    }
    const { person, text } = s.run;
    return act(key, `${s.who}: "${text}"`, () => api.demoInbound(token, person, text));
  }

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        eyebrow="Demo controls"
        title="Run the demo"
        description="Play each person's texts and sensor events without phones. Use this if Photon is down on stage. Everything here is labeled simulated."
      />

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="flex flex-col gap-3 rounded-2xl border border-border bg-surface p-5">
          <label htmlFor="admin-token" className="flex items-center gap-2 font-bold text-ink">
            <KeyRound size={18} className="text-primary" aria-hidden="true" /> Admin token
          </label>
          <input
            id="admin-token"
            type="password"
            autoComplete="off"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder={USE_MOCKS ? "Not needed with demo data" : "Paste the demo admin token"}
            className="min-h-11 rounded-[10px] border border-border bg-surface px-3 text-ink placeholder:text-muted"
          />
          <p className="text-sm text-muted">
            {USE_MOCKS
              ? "Demo data mode: actions run against the in-browser mock backend."
              : "Kept only in this tab. The backend must run with DEMO_MODE=true."}
          </p>
        </div>
        <div className="flex flex-col gap-2 rounded-2xl border border-border bg-surface p-5">
          <p className="font-bold text-ink">System status</p>
          {health ? (
            <dl className="grid grid-cols-2 gap-2 text-sm">
              {[
                ["Backend", health.status],
                ["Messaging", health.messaging === "photon" ? "Photon iMessage" : "Console (simulated)"],
                ["AI", health.ai === "gemini" ? "Gemini" : "Rule-based fallback"],
                ["Demo mode", health.demo_mode ? "On" : "Off"],
              ].map(([k, v]) => (
                <div key={k} className="rounded-lg bg-inset px-3 py-2">
                  <dt className="text-muted">{k}</dt>
                  <dd className="font-semibold text-ink">{v}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-body">Backend not reachable.</p>
          )}
        </div>
      </section>

      <section className="flex flex-col gap-3" aria-labelledby="script-title">
        <h2 id="script-title" className="flex items-center gap-2 text-lg font-bold text-ink">
          <Sparkles size={18} className="text-sun" aria-hidden="true" /> Stage script
        </h2>
        <ol className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {SCRIPT.map((s, i) => (
            <li key={i}>
              <ScriptButton step={s} busy={busy === `s${i}`} disabled={!canAct || busy !== null} onClick={() => runStep(s, `s${i}`)} />
            </li>
          ))}
        </ol>
        <p className="mt-2 text-sm font-semibold text-muted">Alternate endings</p>
        <ol className="grid grid-cols-1 gap-2 md:grid-cols-3">
          {EXTRAS.map((s, i) => (
            <li key={i}>
              <ScriptButton step={s} busy={busy === `x${i}`} disabled={!canAct || busy !== null} onClick={() => runStep(s, `x${i}`)} />
            </li>
          ))}
        </ol>
      </section>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section className="flex flex-col gap-3 rounded-2xl border border-border bg-surface p-5" aria-labelledby="sensor-title">
          <h2 id="sensor-title" className="flex items-center gap-2 text-lg font-bold text-ink">
            <Activity size={18} className="text-primary" aria-hidden="true" /> Sensor replay
          </h2>
          <p className="text-sm text-body">Runs a labeled synthetic motion trace through the fall detector.</p>
          <div className="flex flex-wrap gap-2">
            {TRACES.map((t) => (
              <button
                key={t}
                type="button"
                disabled={!canAct || busy !== null}
                onClick={() => act(`t${t}`, `Replayed "${t.replace("_", " ")}" trace`, async () => setFall(await api.demoFall(token, parentId, t)))}
                className={t === "fall" ? btn.primary : btn.outline}
              >
                <Play size={16} aria-hidden="true" /> {t.replace("_", " ")}
              </button>
            ))}
          </div>
          {fall && (
            <p className="rounded-xl bg-inset p-3 text-sm text-body">
              Detector: <span className="font-semibold text-ink">{fall.detection.label.replace("_", " ")}</span> · score{" "}
              {fall.detection.score.toFixed(2)} (not a clinical probability) · {fall.ingest?.incident_id ? "incident opened" : "no alert"} · simulated
            </p>
          )}
        </section>

        <SendAs people={people} disabled={!canAct || busy !== null} onSend={(pid, text, name) => act("send", `${name}: "${text}"`, () => api.demoInbound(token, pid, text))} />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <section className="flex flex-col gap-3 rounded-2xl border border-border bg-surface p-5" aria-labelledby="outbox-title">
          <h2 id="outbox-title" className="flex items-center gap-2 text-lg font-bold text-ink">
            <Inbox size={18} className="text-primary" aria-hidden="true" /> What FarCare sent
          </h2>
          {outbox.length === 0 ? (
            <p className="text-body">{canAct ? "No messages yet." : "Enter the admin token to see messages."}</p>
          ) : (
            <ul className="flex max-h-[28rem] flex-col gap-2 overflow-y-auto pr-1">
              {outbox.map((m, i) => (
                <li key={i} className="rounded-xl bg-inset p-3">
                  <p className="flex flex-wrap items-center justify-between gap-2 text-sm">
                    <span className="font-semibold text-ink">
                      To {m.to ?? "unknown"} <span className="font-normal text-muted">({m.to_role ?? "?"})</span>
                    </span>
                    <span className="text-muted">
                      {m.channel} · <TimeAgo iso={m.created_at} />
                    </span>
                  </p>
                  <p className="mt-1 text-body">{m.text}</p>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="flex flex-col gap-3 rounded-2xl border border-border bg-surface p-5" aria-labelledby="log-title">
          <h2 id="log-title" className="text-lg font-bold text-ink">
            Control log
          </h2>
          <ul className="flex flex-col gap-1.5 text-sm" aria-live="polite">
            {log.length === 0 && <li className="text-muted">Actions you run appear here.</li>}
            {log.map((l) => (
              <li key={l.id} className={l.ok ? "text-body" : "font-semibold text-danger"}>
                {l.text}
              </li>
            ))}
          </ul>
          <div className="mt-auto border-t border-border pt-4">
            {confirmReset ? (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold text-ink">Clear all requests, alerts and messages?</span>
                <button
                  type="button"
                  className={btn.primary}
                  onClick={() => {
                    setConfirmReset(false);
                    void act("reset", "Demo reset", () => api.demoReset(token));
                  }}
                >
                  Yes, reset
                </button>
                <button type="button" className={btn.outline} onClick={() => setConfirmReset(false)}>
                  Cancel
                </button>
              </div>
            ) : (
              <button type="button" className={btn.outline} disabled={!canAct || busy !== null} onClick={() => setConfirmReset(true)}>
                <RotateCcw size={16} aria-hidden="true" /> Reset demo
              </button>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

function ScriptButton({ step: s, busy, disabled, onClick }: { step: Step; busy: boolean; disabled: boolean; onClick: () => void }) {
  const wait = s.run.kind === "wait";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled && !wait}
      className="flex min-h-14 w-full items-center gap-3 rounded-xl border border-border bg-surface p-3 text-left hover:border-primary disabled:opacity-60"
    >
      <span className="grid size-8 shrink-0 place-items-center rounded-full bg-primary-tint text-sm font-bold text-on-tint">{s.n}</span>
      <span className="min-w-0">
        <span className="block text-xs font-bold uppercase tracking-wide text-muted">{s.who}</span>
        <span className="block font-medium text-ink">{busy ? "Running…" : wait ? s.label : `“${s.label}”`.replace(/^“(Replay.*)”$/, "$1")}</span>
      </span>
    </button>
  );
}

function SendAs({ people, disabled, onSend }: { people: DemoPerson[]; disabled: boolean; onSend: (personId: string, text: string, name: string) => void }) {
  const texters = people.filter((p) => p.role !== "admin");
  const [personId, setPersonId] = useState("parent_001");
  const [text, setText] = useState("");
  function submit(e: FormEvent) {
    e.preventDefault();
    const person = texters.find((p) => p.person_id === personId);
    if (!text.trim() || !person) return;
    onSend(personId, text.trim(), person.display_name);
    setText("");
  }
  return (
    <section className="flex flex-col gap-3 rounded-2xl border border-border bg-surface p-5" aria-labelledby="sendas-title">
      <h2 id="sendas-title" className="flex items-center gap-2 text-lg font-bold text-ink">
        <MessageSquareText size={18} className="text-primary" aria-hidden="true" /> Send a text as…
      </h2>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Sender">
          {texters.map((p) => (
            <label
              key={p.person_id}
              className={`inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-full border px-3 text-sm font-semibold ${
                personId === p.person_id ? "border-primary bg-primary-tint text-on-tint" : "border-border text-body"
              }`}
            >
              <input type="radio" name="sender" value={p.person_id} checked={personId === p.person_id} onChange={() => setPersonId(p.person_id)} className="sr-only" />
              {p.display_name} <span className="font-normal opacity-75">({p.role})</span>
              {!p.reachable && <span className="text-watch-label">· not reachable</span>}
            </label>
          ))}
        </div>
        <label htmlFor="sendas-text" className="sr-only">
          Message
        </label>
        <div className="flex flex-wrap gap-2">
          <input
            id="sendas-text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Type what they'd text FarCare"
            className="min-h-11 min-w-0 flex-[1_1_200px] rounded-[10px] border border-border bg-surface px-3 text-ink placeholder:text-muted"
          />
          <button type="submit" className={btn.primary} disabled={disabled || !text.trim()}>
            <Send size={16} aria-hidden="true" /> Send
          </button>
        </div>
      </form>
    </section>
  );
}

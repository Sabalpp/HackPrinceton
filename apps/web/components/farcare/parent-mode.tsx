"use client";

import { useState } from "react";
import { Check, Pause, Play } from "lucide-react";
import { farcareApi } from "@/lib/farcare-api";
import { nepaliCallTime } from "@/lib/time";
import { MOCK_STATE, useFarCareState } from "@/lib/use-farcare-state";
import { PhoneFrame } from "./phone-frame";

/*
 * Parent mode on Ama's home phone. All Nepali, very large, no navigation or settings.
 * English glosses for reviewers: "I'm OK", "I need help", "I took my morning BP medicine",
 * "Next call: today at 7 in the evening".
 */
type Sent = "idle" | "sending" | "ok" | "help" | "error";

export function ParentMode() {
  const result = useFarCareState();
  const s = result.phase === "ready" ? result.data : MOCK_STATE;
  const [playing, setPlaying] = useState(false);
  const [sent, setSent] = useState<Sent>("idle");
  const [took, setTook] = useState(false);

  async function press(kind: "ok" | "help") {
    setSent("sending");
    try {
      await (kind === "ok" ? farcareApi.parentOk() : farcareApi.parentHelp());
      setSent(kind);
    } catch {
      setSent("error");
    }
  }

  return (
    <PhoneFrame label="आमाको फोन" lang="ne">
      <div className="flex flex-1 flex-col gap-5 overflow-y-auto bg-bg p-5 font-ne">
        {/* Voice note from the child. The prototype has no audio file; the button only toggles. */}
        <div className="flex items-center gap-4 rounded-2xl border border-border bg-surface p-4">
          <span className="grid size-14 shrink-0 place-items-center rounded-full bg-primary-tint text-2xl font-bold text-on-tint" aria-hidden="true">
            स
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-xl font-bold text-ink">सक्षम</p>
            <p className="text-xl text-body">आमा, आइतबार फोन गर्छु।</p>
          </div>
          <button
            type="button"
            onClick={() => setPlaying((p) => !p)}
            aria-pressed={playing}
            aria-label={playing ? "रोक्नुहोस्" : "आवाज सन्देश सुन्नुहोस्"}
            className="grid size-16 shrink-0 place-items-center rounded-full bg-primary text-on-primary hover:bg-primary-hover"
          >
            {playing ? <Pause size={30} aria-hidden="true" /> : <Play size={30} className="ml-1" aria-hidden="true" />}
          </button>
        </div>

        <button
          type="button"
          onClick={() => press("ok")}
          disabled={sent === "sending"}
          className="flex min-h-28 flex-col items-center justify-center rounded-3xl bg-primary px-4 text-on-primary hover:bg-primary-hover disabled:opacity-70"
        >
          <span className="text-4xl font-bold">म ठीक छु</span>
          <span lang="en" className="font-sans text-lg text-on-primary/80">
            I&apos;m OK
          </span>
        </button>

        <button
          type="button"
          onClick={() => press("help")}
          disabled={sent === "sending"}
          className="flex min-h-28 flex-col items-center justify-center rounded-3xl bg-danger px-4 text-white hover:brightness-95 disabled:opacity-70"
        >
          <span className="text-4xl font-bold">मद्दत चाहियो</span>
          <span lang="en" className="font-sans text-lg text-danger-light">
            I need help
          </span>
        </button>

        <p role="status" className="min-h-8 text-center text-xl font-semibold text-ink">
          {sent === "sending" && "पठाउँदै…"}
          {sent === "ok" && "धन्यवाद आमा! परिवारलाई खबर गरियो।"}
          {sent === "help" && "परिवारलाई खबर गरियो। फोन नजिकै राख्नुहोस्।"}
          {sent === "error" && "पठाउन सकिएन। फेरि थिच्नुहोस्।"}
        </p>

        <label className="flex min-h-20 cursor-pointer items-center gap-4 rounded-2xl border-2 border-border bg-surface p-4 has-[:checked]:border-primary has-[:checked]:bg-primary-tint">
          <input type="checkbox" checked={took} onChange={(e) => setTook(e.target.checked)} className="peer sr-only" />
          <span
            className="grid size-11 shrink-0 place-items-center rounded-lg border-2 border-muted bg-surface text-on-primary peer-checked:border-primary peer-checked:bg-primary peer-focus-visible:outline peer-focus-visible:outline-3 peer-focus-visible:outline-sun"
            aria-hidden="true"
          >
            {took && <Check size={28} strokeWidth={3} />}
          </span>
          <span className="text-2xl font-semibold text-ink">बिहानको प्रेसरको औषधि खाएँ</span>
        </label>

        <p className="mt-auto pt-2 text-center text-xl text-body">
          अर्को फोन: आज {nepaliCallTime(s.parent.call_time)}
        </p>
      </div>
    </PhoneFrame>
  );
}

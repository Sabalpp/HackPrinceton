"use client";

import type { ReactNode } from "react";
import type { FarCareState } from "@/lib/farcare-state";
import { useFarCareState } from "@/lib/use-farcare-state";

/** Loading / error / ready wrapper so every page handles the API being down. */
export function StateGate({ children }: { children: (state: FarCareState) => ReactNode }) {
  const result = useFarCareState();
  if (result.phase === "loading") {
    return (
      <div aria-busy="true" aria-label="Loading" className="flex flex-col gap-4">
        <div className="h-8 w-64 animate-pulse rounded-lg bg-border" />
        <div className="h-40 animate-pulse rounded-2xl bg-border/70" />
        <div className="h-64 animate-pulse rounded-2xl bg-border/50" />
      </div>
    );
  }
  if (result.phase === "error") {
    return (
      <div role="alert" className="rounded-2xl border border-border bg-surface p-6">
        <p className="font-semibold text-ink">FarCare is offline</p>
        <p className="mt-1 text-body">{result.message} Ama&apos;s daily call still runs. Try again in a moment.</p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="mt-4 inline-flex min-h-11 items-center rounded-[10px] border border-border px-4 font-semibold text-ink"
        >
          Try again
        </button>
      </div>
    );
  }
  return <>{children(result.data)}</>;
}

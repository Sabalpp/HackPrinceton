"use client";

import { useSyncExternalStore } from "react";

/*
 * One shared 1-second ticker for clocks, countdowns and "5 min ago" labels.
 *
 * - Returns null during server prerender, so nothing time-dependent lands in the static shell
 *   (cacheComponents forbids Date.now() at prerender) and hydration never mismatches.
 * - `stepMs` rounds the snapshot, so a component that only needs minute precision re-renders
 *   once a minute, not every second (React bails out when the snapshot is unchanged).
 * - Use it in small leaf components (see components/ui/time.tsx), never at the page root.
 */
let now = Date.now();
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (!timer) {
    now = Date.now();
    timer = setInterval(() => {
      now = Date.now();
      listeners.forEach((l) => l());
    }, 1000);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}

export function useNow(stepMs = 1000): number | null {
  return useSyncExternalStore(
    subscribe,
    () => Math.floor(now / stepMs) * stepMs,
    () => null,
  );
}

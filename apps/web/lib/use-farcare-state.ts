"use client";

import { useEffect, useState } from "react";
import { API_URL, USE_MOCKS } from "./api";
import type { FarCareState } from "./farcare-state";
import { MOCK_STATE } from "./mock-state";

export { MOCK_STATE };

export type StateResult =
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "ready"; data: FarCareState };

/**
 * The single source of dashboard data.
 * NEXT_PUBLIC_USE_MOCKS=true -> mock/state.json (synchronously, so pages prerender fully);
 * otherwise GET {NEXT_PUBLIC_API_URL}/api/state.
 */
export function useFarCareState(): StateResult {
  const [result, setResult] = useState<StateResult>(
    USE_MOCKS ? { phase: "ready", data: MOCK_STATE } : { phase: "loading" },
  );

  useEffect(() => {
    if (USE_MOCKS) return;
    let alive = true;
    fetch(`${API_URL}/api/state`, { cache: "no-store" })
      .then((res) => {
        if (!res.ok) throw new Error(`FarCare returned ${res.status}`);
        return res.json() as Promise<FarCareState>;
      })
      .then(
        (data) => alive && setResult({ phase: "ready", data }),
        () => alive && setResult({ phase: "error", message: "We can't reach FarCare right now." }),
      );
    return () => {
      alive = false;
    };
  }, []);

  return result;
}

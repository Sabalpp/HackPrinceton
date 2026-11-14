"use client";

import { useEffect, useState } from "react";
import { api, API_URL, USE_MOCKS, type SystemHealth } from "@/lib/api";

/** Placeholder proving the web <-> API wiring. Saksham owns the real UI. */
export function ConnectionStatus() {
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.health().then(setHealth, (e: Error) => setError(e.message));
  }, []);

  return (
    <div className="rounded-xl border border-zinc-200 p-4 text-sm dark:border-zinc-800">
      <p className="font-medium">
        Backend: {USE_MOCKS ? "mock data" : API_URL}
      </p>
      {health && (
        <p className="mt-1 text-zinc-600 dark:text-zinc-400">
          status {health.status} · messaging {health.messaging} · AI {health.ai} · demo{" "}
          {String(health.demo_mode)}
        </p>
      )}
      {error && <p className="mt-1 text-red-600">Not reachable: {error}</p>}
    </div>
  );
}

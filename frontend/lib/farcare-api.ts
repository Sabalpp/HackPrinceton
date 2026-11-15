/**
 * Write calls for the daily-call product. Endpoints are PROPOSED (not in contracts yet).
 * In mock mode they resolve locally so the UI flow can be demoed without a backend.
 */
import { API_URL, USE_MOCKS } from "./api";

async function post(path: string, body?: unknown): Promise<void> {
  if (USE_MOCKS) {
    await new Promise((r) => setTimeout(r, 300));
    return;
  }
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} for ${path}`);
}

export const farcareApi = {
  callAnswer: (text: string) => post("/api/call/answer", { text }),
  parentOk: () => post("/api/parent/ok"),
  parentHelp: () => post("/api/parent/help"),
};

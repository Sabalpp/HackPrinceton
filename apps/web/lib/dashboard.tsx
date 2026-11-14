"use client";

/**
 * One data store for every dashboard page:
 *   GET /api/me -> parent id -> GET /overview + GET /timeline, then the live stream (SSE or mock).
 * Each timeline event is prepended to the feed, bumps `version` (pages refetch their own data
 * when it changes) and triggers one debounced overview refetch.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  api,
  ApiError,
  subscribeTimeline,
  USE_MOCKS,
  VIEWER_TOKEN,
  type Me,
  type ParentOverview,
  type StreamStatus,
  type TimelineEvent,
} from "./api";

export type Connection = "mock" | StreamStatus | "offline";

type Phase =
  | { phase: "loading" }
  | { phase: "error"; status: number; message: string }
  | { phase: "ready"; me: Me; parentId: string; overview: ParentOverview };

type DashboardValue = {
  state: Phase;
  timeline: TimelineEvent[];
  connection: Connection;
  /** Increments on every live event; use it as an effect dependency to refetch. */
  version: number;
  lastEvent: TimelineEvent | null;
  token: string;
  refresh: () => void;
  retry: () => void;
};

const Ctx = createContext<DashboardValue | null>(null);

function describe(err: unknown): { status: number; message: string } {
  if (err instanceof ApiError) {
    if (err.status === 0) return { status: 0, message: "We can't reach FarCare right now." };
    if (err.status === 401 || err.status === 403 || err.status === 404)
      return { status: err.status, message: "You don't have access to this family's dashboard." };
    return { status: err.status, message: "FarCare had a problem loading this page." };
  }
  return { status: -1, message: "Something went wrong loading this page." };
}

async function loadAll(token: string) {
  const me = await api.me(token);
  const parentId = me.parent_ids[0];
  if (!parentId) throw new ApiError(403, "No parent linked to this account");
  const [overview, timeline] = await Promise.all([api.overview(token, parentId), api.timeline(token, parentId, 100)]);
  return { me, parentId, overview, timeline };
}

export function DashboardProvider({ children }: { children: ReactNode }) {
  const token = VIEWER_TOKEN;
  const [state, setState] = useState<Phase>({ phase: "loading" });
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [connection, setConnection] = useState<Connection>(USE_MOCKS ? "mock" : "connecting");
  const [version, setVersion] = useState(0);
  const [lastEvent, setLastEvent] = useState<TimelineEvent | null>(null);
  const ticket = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(() => {
    const t = ++ticket.current;
    return loadAll(token).then(
      ({ me, parentId, overview, timeline: events }) => {
        if (t !== ticket.current) return;
        setState({ phase: "ready", me, parentId, overview });
        setTimeline(events);
      },
      (err: unknown) => {
        if (t !== ticket.current) return;
        const d = describe(err);
        if (d.status === 0 && !USE_MOCKS) setConnection("offline");
        // Keep the last good data on screen if a background refetch fails.
        setState((prev) => (prev.phase === "ready" ? prev : { phase: "error", ...d }));
      },
    );
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  const parentId = state.phase === "ready" ? state.parentId : null;

  useEffect(() => {
    if (!parentId) return;
    const off = subscribeTimeline(
      token,
      parentId,
      (event) => {
        setLastEvent(event);
        setTimeline((list) => (list.some((e) => e.id === event.id) ? list : [event, ...list]));
        setVersion((v) => v + 1);
        // Coalesce bursts (one state change often emits several events) into one refetch.
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => void load(), 250);
      },
      setConnection,
    );
    return () => {
      off();
      if (timer.current) clearTimeout(timer.current);
    };
  }, [token, parentId, load]);

  const retry = useCallback(() => {
    setState({ phase: "loading" });
    void load();
  }, [load]);

  const value = useMemo<DashboardValue>(
    () => ({ state, timeline, connection, version, lastEvent, token, refresh: () => void load(), retry }),
    [state, timeline, connection, version, lastEvent, token, load, retry],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useDashboard(): DashboardValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useDashboard must be used inside <DashboardProvider>");
  return v;
}

/**
 * Fetch page-specific data (e.g. all incidents) and refetch on every live event.
 * `fetcher` must be stable per its deps; pass deps that identify the request.
 */
export function useLiveQuery<T>(fetcher: ((token: string, parentId: string) => Promise<T>) | null) {
  const { state, token, version } = useDashboard();
  const parentId = state.phase === "ready" ? state.parentId : null;
  const [result, setResult] = useState<{ data: T | undefined; error: string | null; loading: boolean }>({
    data: undefined,
    error: null,
    loading: true,
  });

  useEffect(() => {
    if (!parentId || !fetcher) return;
    let alive = true;
    fetcher(token, parentId).then(
      (data) => alive && setResult({ data, error: null, loading: false }),
      (err: unknown) => alive && setResult((r) => ({ data: r.data, error: describe(err).message, loading: false })),
    );
    return () => {
      alive = false;
    };
  }, [parentId, token, version, fetcher]);

  return result;
}

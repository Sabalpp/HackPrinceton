"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

export function usePortalData<T>(fetcher: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(false);
  const sequence = useRef(0);
  const refresh = useCallback(() => {
    const ticket = ++sequence.current;
    return fetcher().then(next => {
      if (alive.current && ticket === sequence.current) { setData(next); setError(null); }
    }, err => {
      if (alive.current && ticket === sequence.current) {
        setError(err instanceof ApiError ? err.message : "We couldn’t refresh this page. Please try again.");
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) setData(null);
      }
    }).finally(() => { if (alive.current && ticket === sequence.current) setLoading(false); });
  }, [fetcher]);
  useEffect(() => {
    alive.current = true;
    void refresh();
    const timer = setInterval(() => { if (!document.hidden) void refresh(); }, 10000);
    const focus = () => { void refresh(); };
    window.addEventListener("focus", focus);
    return () => { alive.current = false; clearInterval(timer); window.removeEventListener("focus", focus); };
  }, [refresh]);
  return { data, loading, error, refresh };
}

/** Retain each action id after a network failure so Retry cannot repeat a mutation. */
export function usePortalAction() {
  const nonces = useRef(new Map<string, string>());
  const inFlight = useRef(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function run<T>(key: string, action: (id: string) => Promise<T>, success: string | ((result:T) => string)): Promise<T | undefined> {
    if (inFlight.current) return;
    inFlight.current = true;
    let id = nonces.current.get(key);
    if (!id) { id = crypto.randomUUID(); nonces.current.set(key, id); }
    setBusy(true); setError(null); setMessage(null);
    try {
      const result = await action(id);
      nonces.current.delete(key);
      setMessage(typeof success === "function" ? success(result) : success);
      return result;
    } catch (err) { setError(err instanceof ApiError ? err.message : "That didn’t finish. Try again with the same details."); }
    finally { inFlight.current = false; setBusy(false); }
  }
  return { busy, message, error, run, clear: () => { setMessage(null); setError(null); } };
}

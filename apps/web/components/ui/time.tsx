"use client";

/*
 * Self-updating time displays. Each subscribes to the shared ticker on its own, so only the
 * text that changes re-renders (never the whole dashboard). All render nothing on the server.
 */
import { clockTime, countdown, relativeTime, weekday, zoneCity } from "@/lib/format";
import { useNow } from "@/lib/use-now";

/** "12 min ago". Updates every 30 s. */
export function TimeAgo({ iso }: { iso: string }) {
  const now = useNow(30_000);
  if (now === null) return null;
  return <time dateTime={iso}>{relativeTime(iso, now)}</time>;
}

/** "1:24 left", or `fallback` once the deadline passes. Updates every second. */
export function Countdown({ iso, fallback }: { iso: string; fallback: string }) {
  const now = useNow(1000);
  if (now === null) return null;
  const left = countdown(iso, now);
  return <span className="tabular">{left ? `${left} left` : fallback}</span>;
}

/** The parent's local time next to the viewer's. Colours inherit from the parent. Updates every 10 s. */
export function TwoClocks({ timeZone, name }: { timeZone: string; name?: string }) {
  const now = useNow(10_000);
  if (now === null) return <p className="h-6" aria-hidden="true" />;
  const theirDay = weekday(now, timeZone);
  const dayDiffers = theirDay !== weekday(now);
  return (
    <p className="tabular">
      <span className="font-semibold">{clockTime(now, timeZone)}</span>
      {dayDiffers && ` ${theirDay}`} {name ? `for ${name} in` : "in"} {zoneCity(timeZone)}
      <span aria-hidden="true"> · </span>
      <span className="sr-only">, </span>
      <span className="whitespace-nowrap opacity-85">{clockTime(now)} for you</span>
    </p>
  );
}

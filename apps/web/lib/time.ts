/** Time-zone helpers. Pure functions: pass `now` in; never read the clock here. */

function zonedParts(tz: string, at: number) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(at);
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0);
  return { y: get("year"), m: get("month"), d: get("day"), h: get("hour"), min: get("minute"), s: get("second") };
}

/** UTC instant for a wall-clock time in `tz` (day overflow is handled by Date.UTC). */
function zonedToInstant(y: number, m: number, d: number, h: number, min: number, tz: string): number {
  const guess = Date.UTC(y, m - 1, d, h, min);
  const p = zonedParts(tz, guess);
  const offset = Date.UTC(p.y, p.m - 1, p.d, p.h, p.min, p.s) - guess;
  return guess - offset;
}

/** "8:57 pm" */
export function clock(at: number, tz: string): string {
  return new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: tz })
    .format(at)
    .replace(/ | /g, " ")
    .replace("AM", "am")
    .replace("PM", "pm");
}

export function hourIn(at: number, tz: string): number {
  return zonedParts(tz, at).h;
}

/** Next daily call: { at, tonight } where tonight = still today in the parent's zone. */
export function nextCall(callTime: string, tz: string, now: number): { at: number; tonight: boolean } {
  const [h, min] = callTime.split(":").map(Number);
  const p = zonedParts(tz, now);
  const today = zonedToInstant(p.y, p.m, p.d, h, min, tz);
  return today > now
    ? { at: today, tonight: true }
    : { at: zonedToInstant(p.y, p.m, p.d + 1, h, min, tz), tonight: false };
}

const asUtc = (day: string) => {
  const [y, m, d] = day.split("-").map(Number);
  return Date.UTC(y, m - 1, d);
};

/** "Saturday, November 14" for a YYYY-MM-DD date. */
export const longDate = (day: string) =>
  new Intl.DateTimeFormat("en-US", { weekday: "long", month: "long", day: "numeric", timeZone: "UTC" }).format(
    asUtc(day),
  );

/** "Nov 12" */
export const shortDate = (day: string) =>
  new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(asUtc(day));

/** "Monday" */
export const weekdayOf = (day: string) =>
  new Intl.DateTimeFormat("en-US", { weekday: "long", timeZone: "UTC" }).format(asUtc(day));

/** Whole days from `from` to `to` (YYYY-MM-DD). */
export const daysBetween = (from: string, to: string) => Math.round((asUtc(to) - asUtc(from)) / 86_400_000);

/** YYYY-MM-DD shifted by n days. */
export const addDays = (day: string, n: number) => new Date(asUtc(day) + n * 86_400_000).toISOString().slice(0, 10);

/** "4h 50m" */
export const hoursMinutes = (min: number) => `${Math.floor(min / 60)}h ${min % 60}m`;

const DEVANAGARI = "०१२३४५६७८९";
export const toDevanagari = (s: string | number) => String(s).replace(/\d/g, (d) => DEVANAGARI[Number(d)]);

/** "बेलुका ७ बजे" for "19:00". */
export function nepaliCallTime(callTime: string): string {
  const [h, m] = callTime.split(":").map(Number);
  const part = h < 12 ? "बिहान" : h < 17 ? "दिउँसो" : h < 20 ? "बेलुका" : "राति";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${part} ${toDevanagari(h12)}${m ? `:${toDevanagari(String(m).padStart(2, "0"))}` : ""} बजे`;
}

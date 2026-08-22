/**
 * One way to render a moment, because there were five.
 *
 * Three screens carried a byte-identical private `formatWhen` -- Approvals,
 * Carrier Transit and Sync Control -- and four more called
 * `new Date(x).toLocaleString()` inline. Bare `toLocaleString()` names no time
 * zone, so an operator in Chennai and one in Frankfurt read the same SLA breach
 * as two different times and neither string says which. Every incoming value is
 * UTC ISO-8601 from the API; the reader's zone is a rendering choice, and it has
 * to be stated for the number to mean anything.
 *
 * These are deliberately plain functions over `Intl`, not a date library. The
 * whole surface is "absolute", "relative", and "how long" -- none of which
 * needs arithmetic beyond subtraction.
 */

/** What every surface shows when a timestamp is genuinely absent. */
export const ABSENT = "\u2014";

/**
 * Cached because constructing an `Intl.DateTimeFormat` is the expensive part
 * and tables call these once per row.
 */
const cache = new Map<string, Intl.DateTimeFormat>();

function formatter(options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = JSON.stringify(options);
  let found = cache.get(key);
  if (found === undefined) {
    found = new Intl.DateTimeFormat(undefined, options);
    cache.set(key, found);
  }
  return found;
}

/**
 * A moment, or the string to show instead of one.
 *
 * Two different failures, kept apart. A null timestamp means the transition has
 * not happened -- `ABSENT`. A value that will not parse means the server sent
 * something unexpected, and the screens that got this right showed the raw
 * value: "—" would claim the field was empty when it was populated with
 * something wrong, and the operator would have nothing to report.
 */
type Resolved = { readonly date: Date } | { readonly fallback: string };

function resolve(value: string | number | Date | null | undefined): Resolved {
  if (value === null || value === undefined || value === "") {
    return { fallback: ABSENT };
  }
  const parsed = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return { fallback: typeof value === "string" ? value : ABSENT };
  }
  return { date: parsed };
}

/**
 * Date and time in the reader's zone, with the zone named.
 *
 * The zone abbreviation is the point. Without it this is the same string the
 * screens produced before, and the same string is what made two operators
 * disagree about when a ticket breached.
 */
export function formatTimestamp(value: string | number | Date | null | undefined): string {
  const resolved = resolve(value);
  if (!("date" in resolved)) {
    return resolved.fallback;
  }
  return formatter({
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(resolved.date);
}

/** Date and time without the zone, for dense tables where the column repeats it. */
export function formatCompactTimestamp(
  value: string | number | Date | null | undefined,
): string {
  const resolved = resolve(value);
  if (!("date" in resolved)) {
    return resolved.fallback;
  }
  return formatter({
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(resolved.date);
}

/** Calendar day only, for anything that is a date rather than a moment. */
export function formatDate(value: string | number | Date | null | undefined): string {
  const resolved = resolve(value);
  if (!("date" in resolved)) {
    return resolved.fallback;
  }
  return formatter({ year: "numeric", month: "short", day: "2-digit" }).format(resolved.date);
}

const UNITS: readonly (readonly [Intl.RelativeTimeFormatUnit, number])[] = [
  ["year", 365 * 24 * 60 * 60],
  ["month", 30 * 24 * 60 * 60],
  ["day", 24 * 60 * 60],
  ["hour", 60 * 60],
  ["minute", 60],
  ["second", 1],
];

let relative: Intl.RelativeTimeFormat | null = null;

/**
 * "3 minutes ago", "in 2 hours".
 *
 * `now` is a parameter rather than a call to `Date.now()` so a component can
 * pass a value it holds in state. Reading the clock during render makes the
 * render impure and produces a different tree on every pass.
 */
export function formatRelative(
  value: string | number | Date | null | undefined,
  now: number = Date.now(),
): string {
  const resolved = resolve(value);
  if (!("date" in resolved)) {
    return resolved.fallback;
  }
  const seconds = (resolved.date.getTime() - now) / 1000;
  relative ??= new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size || unit === "second") {
      return relative.format(Math.round(seconds / size), unit);
    }
  }
  return relative.format(0, "second");
}

/**
 * "4m 12s". For elapsed and remaining durations, where a relative phrase reads
 * wrong -- a run has been going for four minutes, it did not start "4 minutes
 * ago" in the sense a timestamp did.
 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return ABSENT;
  }
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;

  if (hours > 0) {
    return `${String(hours)}h ${String(minutes)}m`;
  }
  if (minutes > 0) {
    return `${String(minutes)}m ${String(rest)}s`;
  }
  return `${String(rest)}s`;
}

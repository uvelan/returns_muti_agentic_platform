import { ABSENT, formatRelative, formatTimestamp } from "./datetime";

/**
 * A support SLA deadline, said in a way that shows whether it has passed.
 *
 * Both places that render one printed the raw ISO string -- no zone, no
 * comparison to now, no tone -- so "2026-08-22T09:30:00Z" told an operator
 * scanning a queue nothing about whether they were late. The only breach signal
 * anywhere was a plain "Breached" row on one screen, styled exactly like every
 * other row.
 *
 * The state is in the words, not only in a colour: an operator who cannot
 * distinguish red from grey, and a screen reader that renders no colour at all,
 * both need to know this one.
 */
export type SlaReading = {
  /** Absolute time, zone named, plus how long until or since. */
  readonly text: string;
  /** True once the deadline has passed. */
  readonly breached: boolean;
};

/**
 * `now` defaults to the clock, matching `formatRelative`. A caller that renders
 * this continuously should pass a value it holds in state instead, so the
 * render stays pure; for a value that changes on refetch, the default is right.
 */
export function readSlaDue(
  value: string | number | Date | null | undefined,
  now: number = Date.now(),
): SlaReading {
  const absolute = formatTimestamp(value);
  if (absolute === ABSENT) {
    return { text: ABSENT, breached: false };
  }

  const parsed = value instanceof Date ? value : new Date(value ?? "");
  if (Number.isNaN(parsed.getTime())) {
    // Unparseable, so `formatTimestamp` is showing the raw value. Claiming a
    // breach state for something we could not read would be worse than silence.
    return { text: absolute, breached: false };
  }

  const breached = parsed.getTime() <= now;
  const relative = formatRelative(parsed, now);
  return {
    text: `${absolute} \u00b7 ${breached ? `breached ${relative}` : `due ${relative}`}`,
    breached,
  };
}

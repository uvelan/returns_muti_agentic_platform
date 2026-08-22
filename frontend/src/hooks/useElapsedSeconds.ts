import { useEffect, useState } from "react";

/**
 * How long the current operation has been running, in whole seconds.
 *
 * Extracted from the copilot, which had the only correct implementation, so the
 * other screens do not each rediscover the two ways it goes wrong. Reading
 * `Date.now()` during render is impure and produces a different tree on every
 * pass; and a value that is not state never re-renders, so the number freezes
 * at whatever it was when something else happened to update.
 *
 * Resets to zero each time `active` becomes true, because the question a
 * waiting operator is asking is "how long has *this* one taken", not "how long
 * since the screen loaded".
 */
export function useElapsedSeconds(active: boolean): number {
  // Start and clock are written together, and only ever from a timer callback.
  // Two earlier shapes were refused for good reasons: resetting a counter in
  // the effect body is the pattern that becomes a render loop when someone adds
  // a dependency, and keeping the start in a ref means reading a ref during
  // render, which is not guaranteed to be the value the render was scheduled
  // for. One state object avoids both, and it cannot hold a start from one run
  // beside a clock from another.
  const [run, setRun] = useState<{ readonly started: number; readonly now: number } | null>(
    null,
  );

  useEffect(() => {
    if (!active) return;
    const started = Date.now();
    const write = () => {
      setRun({ started, now: Date.now() });
    };
    // Through a timer rather than called here, so the effect body itself writes
    // nothing. Zero delay, so a second operation shows 0s rather than the
    // previous one's duration for the first second.
    const immediate = setTimeout(write, 0);
    const tick = setInterval(write, 1000);
    return () => {
      clearTimeout(immediate);
      clearInterval(tick);
    };
  }, [active]);

  if (!active || run === null) return 0;
  return Math.max(0, Math.floor((run.now - run.started) / 1000));
}

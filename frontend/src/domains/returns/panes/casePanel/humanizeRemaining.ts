/**
 * "4 minutes left", never "00:04:12".
 *
 * A branch associate is not watching a stopwatch -- they are deciding whether
 * to read this now or after the customer standing in front of them. Seconds of
 * precision on a forty-minute deadline is noise, and it also forces a re-render
 * every second for a digit nobody reads at that resolution.
 *
 * Its own module rather than an export from the component, because the console
 * only fast-refreshes a file that exports components alone -- and a shared
 * helper that quietly costs everyone their hot reload is a worse trade than a
 * second file.
 */
export function humanizeRemaining(milliseconds: number): string {
  if (milliseconds <= 0) return "Deadline passed";
  const minutes = Math.round(milliseconds / 60_000);
  if (minutes < 1) return "Less than a minute left";
  if (minutes === 1) return "1 minute left";
  if (minutes < 90) return `${String(minutes)} minutes left`;
  const hours = Math.round(minutes / 60);
  return hours === 1 ? "About 1 hour left" : `About ${String(hours)} hours left`;
}

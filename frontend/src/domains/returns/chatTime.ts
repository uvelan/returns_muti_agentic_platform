import type { ChatHistoryEntry } from "./panes/ConversationPane";

/**
 * The clock time of an entry, in the viewer's zone, or nothing.
 *
 * Time only, not date: a return is worked in one sitting, and the day is on
 * the case panel. `Invalid Date` -- an `at` that is not an instant -- renders
 * nothing rather than "Invalid Date" on an associate's screen.
 */
export function saidAt(at: string | undefined): string | null {
  if (at === undefined) return null;
  const instant = new Date(at);
  if (Number.isNaN(instant.getTime())) return null;
  return instant.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/**
 * `arriving` merged into `history` by instant, each list's own order kept.
 *
 * An arriving entry goes before the first history entry said after it. One
 * with no instant, or one that follows nothing dated, goes to the end -- the
 * old behaviour, kept for records that predate instants. Pure and stable, so
 * the same inputs draw the same list on every render.
 */
export function placedByTime(
  history: readonly ChatHistoryEntry[],
  arriving: readonly ChatHistoryEntry[],
): ChatHistoryEntry[] {
  const instant = (entry: ChatHistoryEntry): number | null => {
    if (entry.at === undefined) return null;
    const value = new Date(entry.at).getTime();
    return Number.isNaN(value) ? null : value;
  };
  const placed: ChatHistoryEntry[] = [...history];
  for (const entry of arriving) {
    const when = instant(entry);
    let position = placed.length;
    if (when !== null) {
      const later = placed.findIndex((existing) => {
        const at = instant(existing);
        return at !== null && at > when;
      });
      if (later !== -1) position = later;
    }
    placed.splice(position, 0, entry);
  }
  return placed;
}

import { useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { RailSlotContext } from "./railSlot";

/**
 * UI-05 -- the contextual rail.
 *
 * Six of the nine domains are one workspace with no sections, so their rail was
 * a back link, a domain name, and eight inches of nothing. The obvious fix --
 * putting every domain back in the rail -- is the one Wave E deliberately
 * removed: a fixed list of all nine on every screen answers "where else could I
 * go", which an operator asks a few times a day, at the cost of the space they
 * look at constantly.
 *
 * So the rail carries *context* instead: what this screen is currently showing,
 * summarised, from the data the screen has already fetched. A portal rather
 * than a prop or a registry field, because that is what keeps it screen-specific
 * -- the copilot knows which return is open, and the shell cannot.
 *
 * **No second fetch.** A rail block that queried on its own would double every
 * screen's load for a summary of what is already on it. Every caller passes
 * values it is already rendering.
 *
 * **Never navigation.** A rail entry that routes nowhere is worse than an absent
 * one, and a rail full of links to screens that do not exist is how a shell
 * starts lying about what the platform has.
 */

/**
 * Render into the domain rail from inside a screen.
 *
 * Renders nothing when there is no slot -- a collapsed rail, or a screen shown
 * outside the shell in a test -- rather than falling back to rendering in place,
 * which would drop a sidebar block into the middle of the workspace.
 */
export function DomainRail({ children }: { children: ReactNode }) {
  const slot = useContext(RailSlotContext);
  return slot === null ? null : createPortal(children, slot);
}

/** One labelled group in the rail. */
export function RailSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5 border-t border-white/10 px-4 py-3">
      <h3 className="text-[11px] font-semibold uppercase tracking-wide text-rail-on-surface/50">
        {title}
      </h3>
      {children}
    </section>
  );
}

/**
 * One line of context.
 *
 * `value` is `null` for "the platform does not know", which is rendered as a
 * dash rather than omitted: a missing row and a row reading "none" are
 * different answers, and on a rail the reader cannot tell which they are
 * looking at unless the label stays.
 */
export function RailFact({ label, value }: { label: string; value: string | number | null }) {
  return (
    <div className="flex min-w-0 items-baseline justify-between gap-2 text-xs">
      <span className="shrink-0 text-rail-on-surface/60">{label}</span>
      <span className="min-w-0 truncate text-rail-on-surface" title={value === null ? undefined : String(value)}>
        {value === null || value === "" ? "--" : String(value)}
      </span>
    </div>
  );
}

/** A short note, for a state that a label/value pair would misrepresent. */
export function RailNote({ children }: { children: ReactNode }) {
  return <p className="text-[11px] leading-snug text-rail-on-surface/60">{children}</p>;
}

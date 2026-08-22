import { useState, type ReactNode } from "react";
import { COPILOT_TOKENS } from "../copilotTokens";

export type ReturnCopilotShellProps = {
  conversationPane: ReactNode;
  progressTruthPane: ReactNode;
  businessObjectPane: ReactNode;
};

const PANES = [
  { id: "conversation", label: "Conversation" },
  { id: "progress", label: "Progress" },
  { id: "return", label: "Return" },
] as const;

type PaneId = (typeof PANES)[number]["id"];

/**
 * ReturnCopilotShell is the single authoritative layout owner of the 3-column
 * 40fr / 24fr / 36fr desktop workspace grid across all 8 lifecycle modes.
 *
 * **Below `lg` it shows one pane at a time.** The grid already collapsed to a
 * single column there, which sounds right and is not: `<main>` is
 * `h-full overflow-hidden`, so three stacked panes divide one viewport height
 * between them. At 320x800 that is 231 pixels each -- about two messages of
 * conversation, above a progress list showing one milestone, above a return
 * record showing its first field. Nothing is lost, because each pane body
 * scrolls, but reading any of it means working three keyholes at once.
 *
 * Tabs are what the frontend scope permits here ("smaller widths may use a
 * drawer and progressive list/detail or tabs"), and they give the active pane
 * the whole height instead of a third of it.
 *
 * **Every pane is rendered once, and hidden with `max-lg:hidden` rather than
 * unmounted.** Two panes and a switch would discard the draft an associate is
 * typing the moment they glanced at Progress; and two *copies* -- one desktop,
 * one mobile -- would put every element in the document twice, which no test
 * and no screen reader can be asked to disambiguate.
 */
export function ReturnCopilotShell({
  conversationPane,
  progressTruthPane,
  businessObjectPane,
}: ReturnCopilotShellProps) {
  const [active, setActive] = useState<PaneId>("conversation");

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      {/*
        A tablist rather than three buttons: the panes are alternative views of
        one case, so this should be heard as "tab 2 of 3" and not as three
        unrelated controls. Absent at `lg`, where all three panes are on screen
        and there is nothing to switch between.
      */}
      <div role="tablist" aria-label="Return workspace" className="flex shrink-0 gap-1 lg:hidden">
        {PANES.map((pane) => (
          <button
            key={pane.id}
            type="button"
            role="tab"
            aria-selected={active === pane.id}
            aria-controls={`copilot-pane-${pane.id}`}
            onClick={() => { setActive(pane.id); }}
            className={`flex-1 rounded-lg px-3 py-2 text-xs font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${
              active === pane.id
                ? "bg-secondary-container text-primary"
                : "border border-outline-control text-on-surface-variant"
            }`}
          >
            {pane.label}
          </button>
        ))}
      </div>

      <div className={`min-h-0 flex-1 ${COPILOT_TOKENS.layout.shell}`}>
        {PANES.map((pane) => (
          <div
            key={pane.id}
            id={`copilot-pane-${pane.id}`}
            // Only below `lg`: at desktop every pane is a column of the grid and
            // none of them is "inactive".
            className={`flex min-h-0 flex-col ${active === pane.id ? "" : "max-lg:hidden"}`}
          >
            {pane.id === "conversation" ? conversationPane : null}
            {pane.id === "progress" ? progressTruthPane : null}
            {pane.id === "return" ? businessObjectPane : null}
          </div>
        ))}
      </div>
    </div>
  );
}

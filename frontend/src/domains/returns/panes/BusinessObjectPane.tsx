import type { ReactNode } from "react";
import type { CopilotLifecycleMode } from "../types";
import { COPILOT_TOKENS } from "../copilotTokens";

export type BusinessObjectPaneProps = {
  activeMode: CopilotLifecycleMode;
  candidateCount?: number;
  children: ReactNode;
};

const MODE_TITLES: Record<CopilotLifecycleMode, { title: string; subtitle: string }> = {
  DISCOVERY: {
    title: "Context",
    subtitle: "Search Guidance",
  },
  CANDIDATE_ORDER: {
    title: "Candidates",
    subtitle: "Matched Orders",
  },
  ITEM_SELECTION: {
    title: "Item Selection",
    subtitle: "Line Item Scope",
  },
  RETURN_EVALUATION: {
    title: "Evaluation",
    subtitle: "Policy & Eligibility",
  },
  AUTHORIZED_RMA: {
    title: "Authorized RMA",
    subtitle: "Manifest & Label",
  },
  CARRIER_TRANSIT: {
    title: "Carrier Transit",
    subtitle: "Tracking & Milestones",
  },
  WAREHOUSE_RECEIVING: {
    title: "Warehouse Dock",
    subtitle: "Receiving & Bay Routing",
  },
  RETURN_SETTLEMENT: {
    title: "Settlement",
    subtitle: "Credit & Completion",
  },
};

/**
 * BusinessObjectPane acts as the top-aligned authoritative container for the
 * active state-specific business object in the 3rd column (36fr).
 */
export function BusinessObjectPane({ activeMode, candidateCount, children }: BusinessObjectPaneProps) {
  const meta = MODE_TITLES[activeMode];
  const title =
    activeMode === "CANDIDATE_ORDER" && typeof candidateCount === "number" && candidateCount > 0
      ? `Candidates (${String(candidateCount)})`
      : meta.title;

  return (
    <section className={COPILOT_TOKENS.layout.pane}>
      {/* 1. Locked Pane Header (52px) */}
      <header className={COPILOT_TOKENS.header.container}>
        <div className="flex items-center justify-between w-full">
          <h2 className={COPILOT_TOKENS.header.title}>{title}</h2>
          <span className="rounded-full bg-secondary-container px-2.5 py-0.5 text-xs font-medium text-primary">
            {meta.subtitle}
          </span>
        </div>
      </header>

      {/* 2. Top-aligned Authoritative Content */}
      <div className={COPILOT_TOKENS.layout.paneBody}>
        {children}
      </div>
    </section>
  );
}

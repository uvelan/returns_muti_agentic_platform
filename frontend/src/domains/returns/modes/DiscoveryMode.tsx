import { FileSearch, Lightbulb, Search, Sparkles } from "lucide-react";
import type { AgentTurnResult } from "../../../api/orderAgent";

export type DiscoveryModeProps = {
  turn?: AgentTurnResult | null;
};

export function DiscoveryMode({ turn }: DiscoveryModeProps) {
  return (
    <div className="flex flex-col gap-4">
      {/* Top Banner / Guidance */}
      <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-outline-variant/50 bg-surface-container-low/40 p-6 text-center shadow-[0_4px_24px_rgba(0,0,0,0.01)]">
        <div className="flex size-12 items-center justify-center rounded-full bg-secondary-container mb-3 text-primary">
          <FileSearch size={22} />
        </div>
        <h3 className="text-sm font-semibold text-on-surface mb-1">
          Discovery Guidance
        </h3>
        <p className="max-w-xs text-xs text-on-surface-variant leading-relaxed">
          {turn == null
            ? "Start by describing the order in the chat. Matches and their evidence appear here."
            : "The agent has not matched an order yet."}
        </p>
      </div>

      {/* Tip 1: Combine Information */}
      <div className="flex items-start gap-3.5 rounded-xl border border-outline-variant/30 bg-surface-container-low p-4 shadow-sm transition hover:border-primary/40">
        <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-secondary-container text-primary">
          <Sparkles size={16} />
        </div>
        <div className="min-w-0 flex-1">
          <h4 className="text-xs font-semibold text-on-surface mb-1">
            Combine Information
          </h4>
          <p className="text-xs text-on-surface-variant leading-relaxed">
            Provide multiple attributes together to locate orders quickly.
          </p>
          <span className="mt-2 inline-block rounded border border-outline-variant/30 bg-surface px-2.5 py-1 text-xs text-outline">
            Include customer name, location, and part description in one query
          </span>
        </div>
      </div>

      {/* Tip 2: Helpful Search Anchors */}
      <div className="flex items-start gap-3.5 rounded-xl border border-outline-variant/30 bg-surface-container-low p-4 shadow-sm transition hover:border-primary/40">
        <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-secondary-container text-primary">
          <Search size={16} />
        </div>
        <div className="min-w-0 flex-1">
          <h4 className="text-xs font-semibold text-on-surface mb-1">
            Helpful Search Anchors
          </h4>
          <p className="text-xs text-on-surface-variant leading-relaxed">
            Search by any known business identifier:
          </p>
          <ul className="mt-2 list-disc list-inside space-y-1 text-xs text-on-surface-variant">
            <li>Sales Order Number</li>
            <li>Customer Name, Email, or Account ID</li>
            <li>Item SKU or Part Description</li>
            <li>Delivery Postal Code or Branch Location</li>
          </ul>
        </div>
      </div>

      {/* Search Assistance Note */}
      <div className="flex items-center gap-2.5 rounded-xl border border-outline-variant/30 bg-surface-container-low/60 p-3.5 text-xs text-outline">
        <Lightbulb size={16} className="text-primary shrink-0" />
        <span>
          Natural language searches are automatically parsed and matched against order graph records.
        </span>
      </div>
    </div>
  );
}

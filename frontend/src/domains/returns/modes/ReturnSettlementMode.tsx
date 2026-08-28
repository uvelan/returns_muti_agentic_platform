import { CheckCircle2, FileText, RotateCcw } from "lucide-react";
import type {
  CaseProjection,
  ReturnRecordProjection,
  SettlementProjection,
} from "../../../api/cases";

/**
 * The credit, which this platform does not issue.
 *
 * `SettlementStatus.NOT_INTEGRATED` is a positive statement, not an absence:
 * nothing here computes a settled amount, issues a credit memo or records a
 * settlement date. The pane used to show `249.99`, `18.75` and `CM-2026-88192`
 * with no backend contract behind a single one of them, and a case reaching
 * this pane is why a completed return must never be counted as a settled one.
 *
 * **Nothing is rendered as `Unavailable` any more.** The four ledger lines have
 * no producer anywhere in the platform, so the pane printed five of them plus a
 * `$ Unavailable` total -- six rows of nothing, under a heading called
 * "Settlement Ledger", above a note explaining that the numbers it did not have
 * would have been approximate anyway. A reader learned the platform's
 * limitations and nothing about the return.
 *
 * So the pane says what it knows: the return is complete, and where the credit
 * stands. A figure appears only when one exists -- `settledAmount`, currency
 * included, because an amount without one is not an amount -- and a credit memo
 * reference only when a memo has one. Absent, they are simply not drawn, which
 * is the honest shape of "this platform does not issue the credit".
 */

export type ReturnSettlementModeProps = {
  settlement?: SettlementProjection | null;
  /** The persisted case status. `COMPLETED_EXTERNAL_SETTLEMENT` is not `COMPLETED`. */
  caseStatus?: CaseProjection["status"] | null;
  /**
   * The RMAs this case holds. What the associate came to this pane to read:
   * the authorisation number, how the goods are coming back, and where each
   * one stands. The pane showed none of it and led with a credit it cannot
   * compute instead.
   */
  returnRecords?: readonly ReturnRecordProjection[];
  onStartNewReturn?: () => void;
  onViewCaseAudit?: () => void;
};

/** A status the projection did not carry. The one thing still worth naming. */
const UNKNOWN = "Unknown";

/** How the completion banner should read, given what the platform actually settled. */
function banner(settlement: SettlementProjection | null): { title: string; detail: string } {
  if (settlement === null) {
    return {
      title: "Return Completed",
      detail: "The platform has published no settlement for this return.",
    };
  }
  if (settlement.status === "SETTLED") {
    return { title: "Return Completed & Settled", detail: "A credit has been recorded." };
  }
  if (settlement.status === "PENDING") {
    return { title: "Return Completed", detail: "Settlement is in progress." };
  }
  return {
    title: "Return Completed · Settlement Not Integrated",
    detail: "Credit is issued outside this platform; no amount is available here.",
  };
}

export function ReturnSettlementMode({
  settlement = null,
  caseStatus = null,
  returnRecords = [],
  onStartNewReturn,
  onViewCaseAudit,
}: ReturnSettlementModeProps) {
  const { title, detail } = banner(settlement);
  //: The case's own resolution, which is the thing the associate came here to
  //: read. `COMPLETED_EXTERNAL_SETTLEMENT` is a completed return whose credit
  //: this platform did not issue -- distinct from `COMPLETED`, and never
  //: rendered as though the two were the same.
  //:
  //: Absent reads `Unknown`, never `Completed`. Defaulting a *status* to the
  //: happy one is the fabrication this domain's guard exists to catch, and it
  //: caught this line: a pane that says a return completed because it was not
  //: told otherwise is worse than one that admits it does not know.
  const returnState = caseStatus ?? UNKNOWN;

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-outline-variant bg-surface-container-low p-4">
      {/* 1. Completion Banner */}
      <div className="flex items-center gap-3 rounded-md bg-secondary-container p-3 text-on-surface">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary text-on-primary">
          <CheckCircle2 size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <span className="block text-xs font-semibold uppercase tracking-wider text-primary">
            {title}
          </span>
          <p className="text-xs text-on-surface-variant mt-0.5">{detail}</p>
        </div>
      </div>

      {/* 2. The RMAs, which are what this pane is for. No credit line of any
          kind: this platform does not compute one, and every row that tried to
          say so said "Unavailable". */}
      {returnRecords.length > 0 ? (
        <div className="flex flex-col gap-2 pt-2 border-t border-outline-variant">
          {returnRecords.map((record) => (
            <div
              key={record.returnReference}
              className="flex flex-col gap-1.5 rounded border border-outline-variant bg-surface-container-lowest p-2.5 text-xs"
            >
              <div className="flex justify-between">
                <span className="text-outline">RMA</span>
                <span className="font-mono font-semibold text-on-surface">
                  {record.returnReference}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-outline">Return Method</span>
                {/* Absent until Support states one. Never defaulted: a method
                    the platform guessed is a method nobody agreed to. */}
                <span className="font-semibold text-on-surface">
                  {record.returnMethod ?? UNKNOWN}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-outline">Return Status</span>
                <span className="font-semibold text-primary">{record.status ?? UNKNOWN}</span>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {/* 3. Where the case itself ended. */}
      <div className="flex justify-between pt-2 border-t border-outline-variant text-xs">
        <span className="text-outline">Case Resolution</span>
        <span className="font-semibold text-on-surface">{returnState}</span>
      </div>

      {/* Final Action Placement */}
      <div className="flex flex-col gap-2 pt-3 border-t border-outline-variant">
        <button
          type="button"
          onClick={() => {
            onStartNewReturn?.();
          }}
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-primary py-2.5 text-xs font-semibold text-on-primary transition hover:bg-primary-container"
        >
          <RotateCcw size={14} />
          <span>Start New Return</span>
        </button>

        <button
          type="button"
          disabled={onViewCaseAudit === undefined}
          onClick={() => {
            onViewCaseAudit?.();
          }}
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-outline-control bg-surface-container-lowest py-2 text-xs font-semibold text-on-surface transition hover:bg-surface-container disabled:opacity-40"
        >
          <FileText size={14} />
          <span>View Full Case Audit History</span>
        </button>
      </div>
    </div>
  );
}

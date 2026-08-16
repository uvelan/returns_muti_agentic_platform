import { CheckCircle2, DollarSign, FileText, RotateCcw } from "lucide-react";
import type { CaseProjection, SettlementProjection } from "../../../api/cases";

/**
 * The credit, which this platform does not issue.
 *
 * `SettlementStatus.NOT_INTEGRATED` is a positive statement, not an absence:
 * nothing here computes a settled amount, issues a credit memo or records a
 * settlement date. The pane used to show `249.99`, `18.75` and `CM-2026-88192`
 * with no backend contract behind a single one of them, and a case reaching
 * this pane is why a completed return must never be counted as a settled one.
 *
 * The four ledger lines have **no producer anywhere in the platform**, so they
 * say `Unavailable` and will keep saying it until one exists. `settledAmount`
 * is the one figure that can ever be real, and it is rendered only when the
 * contract carries it -- currency included, because an amount without one is
 * not an amount.
 */

export type ReturnSettlementModeProps = {
  settlement?: SettlementProjection | null;
  /** The persisted case status. `COMPLETED_EXTERNAL_SETTLEMENT` is not `COMPLETED`. */
  caseStatus?: CaseProjection["status"] | null;
  onStartNewReturn?: () => void;
  onViewCaseAudit?: () => void;
};

/** No producer, as opposed to a producer that has not run. */
const UNAVAILABLE = "Unavailable";

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
  onStartNewReturn,
  onViewCaseAudit,
}: ReturnSettlementModeProps) {
  const { title, detail } = banner(settlement);
  const amount = settlement?.settledAmount ?? null;
  const netCredit =
    amount === null ? UNAVAILABLE : `${amount.amount} ${amount.currency}`;
  const memoRef = settlement?.creditMemoReference ?? UNAVAILABLE;
  const status = settlement?.status ?? caseStatus ?? UNAVAILABLE;

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

      {/* 2. Settlement Financial Breakdown */}
      <div className="flex flex-col gap-2 pt-2 border-t border-outline-variant">
        <span className="text-xs font-semibold uppercase tracking-wider text-outline">
          Settlement Ledger
        </span>

        <dl className="flex flex-col gap-2 text-xs">
          {(
            [
              "Original Order Total",
              "Item Credit Subtotal",
              "Restocking Fee",
              "Tax Adjustment",
            ] as const
          ).map((label) => (
            <div key={label} className="flex justify-between border-b border-outline-variant/50 pb-1.5">
              <dt className="text-outline">{label}</dt>
              {/* No producer computes any of these. A figure here would be
                  invented, and an invented figure on a credit line is the
                  worst kind. */}
              <dd className="font-semibold text-on-surface">{UNAVAILABLE}</dd>
            </div>
          ))}

          {/* Every figure this platform puts on a screen is labelled approximate,
              by operator instruction (2026-08-15). Nothing here is a commitment:
              the restocking rate is seller configuration, no producer computes a
              refund, and settlement happens in a system this one only reports.
              An operator reading a number off this screen must not treat it as
              the amount a customer will receive. */}
          <div className="flex justify-between items-center pt-1">
            <dt className="text-xs font-bold text-on-surface">Completed</dt>
            <dd className="text-base font-bold text-primary flex items-center">
              <DollarSign size={16} />
              <span>{netCredit}</span>
            </dd>
          </div>
          <p className="text-[11px] text-outline leading-snug">
            Approximate. Amounts are indicative only and are settled outside this platform.
          </p>
        </dl>
      </div>

      {/* Credit Memo Metadata */}
      <div className="rounded border border-outline-variant bg-surface-container-lowest p-2.5 text-xs">
        <div className="flex justify-between">
          <span className="text-outline">Credit Memo Ref</span>
          <span className="font-mono font-semibold text-on-surface">{memoRef}</span>
        </div>
        <div className="flex justify-between mt-1">
          <span className="text-outline">Settlement Status</span>
          <span className="font-semibold text-primary">{status}</span>
        </div>
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
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-outline-variant bg-surface-container-lowest py-2 text-xs font-semibold text-on-surface transition hover:bg-surface-container disabled:opacity-40"
        >
          <FileText size={14} />
          <span>View Full Case Audit History</span>
        </button>
      </div>
    </div>
  );
}

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import type { ReturnHistory } from "../../../api/returnHistory";
import { ReturnHistorySection } from "./ReturnHistorySection";

export type CandidateOrderModeProps = {
  candidates: readonly Record<string, unknown>[];
  /**
   * How many the search actually matched, as the graph reported it.
   *
   * **Not `candidates.length`.** The agent serves a bounded page -- five rows,
   * with at most twenty-five cached -- and a table that headed itself "12 orders
   * matched" while the search found four hundred would contradict the agent's
   * own rules in the pane next to it. `null` when the evidence carried no total,
   * in which case the header says only how many are on screen.
   */
  totalFound?: number | null;
  returnHistory: ReturnHistory | null;
  returnHistoryPending: boolean;
  returnHistoryError: Error | null;
  onSelectCandidate?: (candidate: Record<string, unknown>) => void;
};

/**
 * How many rows are drawn at once, and how many each reveal adds.
 *
 * The list is as long as the search made it, so it needs a bound that is a
 * design decision rather than a data one. The count of what is not shown is
 * always stated -- a truncation nobody is told about is data quietly lost.
 */
const ROW_PAGE = 10;

function scalar(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "-";
}

/**
 * Candidate fields that must never be rendered, by operator instruction
 * (2026-08-15): the customer's internal ERP number is not for the screen.
 *
 * Columns are derived from the graph result's own keys, so anything a query
 * selects appears here by default -- which is why suppression has to be an
 * explicit list rather than a matter of choosing columns. The value is **not**
 * stripped from the candidate object: `onSelectCandidate` and the return-history
 * lookup both need `customer_id`, since an ERP customer number is only unique
 * within an account. It is withheld from display and from anything written into
 * the conversation, not from the client's own reasoning.
 */
const SUPPRESSED_COLUMNS = new Set([
  // The internal ERP customer number is not for the screen.
  "customer_id",
  // Extract housekeeping, not a fact about the order. It says when the source
  // system last rewrote the row -- which tells an associate choosing between
  // five Alvarados nothing, and reads like an order date beside one.
  "source_updated_at",
]);

export function CandidateOrderMode({
  candidates,
  totalFound = null,
  returnHistory,
  returnHistoryPending,
  returnHistoryError,
  onSelectCandidate,
}: CandidateOrderModeProps) {
  const [visible, setVisible] = useState<number>(ROW_PAGE);

  if (candidates.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center">
        <p className="max-w-xs text-xs text-on-surface-variant">
          The agent has not matched an order yet.
        </p>
      </div>
    );
  }

  const shown = candidates.slice(0, visible);
  const hiddenOnPage = candidates.length - shown.length;
  // Only the rows the agent actually handed over can be counted as unshown
  // here; anything beyond `candidates.length` is on a page the browser cannot
  // fetch -- only the agent can, by searching again for more results.
  const beyondPage = totalFound === null ? 0 : Math.max(0, totalFound - candidates.length);

  const columns = [...new Set(shown.flatMap((row) => Object.keys(row)))].filter(
    (column) => !SUPPRESSED_COLUMNS.has(column),
  );

  return (
    <div className="flex flex-col gap-3">
      {/* Header Bar */}
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wider text-outline">
          Select Matching Order
        </span>
        <span className="rounded-full bg-secondary-container px-2.5 py-0.5 text-xs font-semibold text-primary">
          {totalFound === null
            ? `Showing ${String(shown.length)} of ${String(candidates.length)}`
            : `Showing ${String(shown.length)} of ${String(totalFound)} matched`}
        </span>
      </div>

      {/* Orders Table */}
      <div className="overflow-x-auto rounded-xl border border-outline-variant/30 bg-surface-container-lowest shadow-sm">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-outline-variant/20 bg-surface-container-low text-outline">
              {columns.map((column) => (
                <th key={column} className="px-3 py-2 font-medium capitalize">
                  {column.replace(/_/g, " ")}
                </th>
              ))}
              <th className="px-3 py-2 text-right font-medium">Action</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((row, index) => (
              <tr
                key={index}
                className="border-t border-outline-variant/10 transition hover:bg-surface-container-low/60"
              >
                {columns.map((column) => (
                  <td key={column} className="px-3 py-2.5 font-medium text-on-surface">
                    {scalar(row[column])}
                  </td>
                ))}
                <td className="px-3 py-2.5 text-right">
                  <button
                    type="button"
                    onClick={() => onSelectCandidate?.(row)}
                    className="inline-flex items-center gap-1 rounded-lg bg-primary-container px-2.5 py-1 text-xs font-semibold text-white shadow-xs transition hover:bg-primary-container/90"
                  >
                    <span>Select</span>
                    <ChevronRight size={12} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {hiddenOnPage > 0 ? (
        <button
          type="button"
          onClick={() => {
            setVisible((count) => count + ROW_PAGE);
          }}
          className="self-start rounded-lg border border-outline-variant/60 bg-surface px-3 py-1.5 text-xs font-semibold text-on-surface"
        >
          Show {String(Math.min(ROW_PAGE, hiddenOnPage))} more · {String(hiddenOnPage)} not shown
        </button>
      ) : null}

      {beyondPage > 0 ? (
        <p className="text-[11px] leading-relaxed text-outline">
          {String(beyondPage)} further match{beyondPage === 1 ? "" : "es"} were found and are not on
          this page. Ask the agent for more results, or narrow the search — the browser holds only
          the page the agent served.
        </p>
      ) : null}

      {/* Past Return History if available */}
      <ReturnHistorySection
        history={returnHistory}
        pending={returnHistoryPending}
        error={returnHistoryError}
      />
    </div>
  );
}

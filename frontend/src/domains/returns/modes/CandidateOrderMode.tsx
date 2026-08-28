import { Fragment, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
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
  /**
   * Whether this row can actually be confirmed. Rows differ in what they
   * carry: an order search yields `sales_order_number`, a customer search
   * yields `customer_name`, and a line query that selected neither yields a row
   * naming an item the agent cannot be told to confirm.
   *
   * The page's `confirmationFor` already returned `null` for that last case and
   * the click did nothing -- an enabled button, no error, no feedback, which is
   * the exact defect its own comment records having fixed for customer rows.
   * The predicate lets this component say so in the markup instead of relying on
   * every caller's handler to fail quietly.
   */
  canSelectCandidate?: (candidate: Record<string, unknown>) => boolean;
  /**
   * The operator's own column choice, from `runtime-config`'s
   * `candidateColumns`. Empty means the deployment has not said, and the
   * built-in `PRIMARY_COLUMNS` answer instead -- a fallback this component can
   * defend, unlike rendering every field the query selected.
   */
  configuredColumns?: readonly { readonly label: string; readonly fields: readonly string[] }[];
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
 * The value is **not** stripped from the candidate object: `onSelectCandidate`
 * and the return-history lookup both need `customer_id`, since an ERP customer
 * number is only unique within an account. It is withheld from display and from
 * anything written into the conversation, not from the client's own reasoning.
 */
const SUPPRESSED_COLUMNS = new Set([
  // The internal ERP customer number is not for the screen.
  "customer_id",
  // Extract housekeeping, not a fact about the order. It says when the source
  // system last rewrote the row -- which tells an associate choosing between
  // five Alvarados nothing, and reads like an order date beside one.
  "source_updated_at",
]);

/**
 * The columns an associate chooses an order by, in the order they read them --
 * and nothing else. A candidate row used to render every field the graph query
 * selected: twenty-one columns of warehouses, PO numbers and timestamps around
 * the five facts that actually identify a return. Those five lead now; the
 * rest wait behind each row's Details control.
 *
 * Each column is a label plus the field aliases that carry it, because the
 * candidate shape depends on what was searched: an order search yields
 * order-header rows (no product, no quantity), a line search yields line rows,
 * a customer search yields customer rows. A column none of the page's rows
 * carry is simply not drawn.
 */
const PRIMARY_COLUMNS: readonly { label: string; fields: readonly string[] }[] = [
  { label: "Order", fields: ["sales_order_number", "order_number"] },
  { label: "Product", fields: ["product_description", "product_name", "description"] },
  { label: "Colour", fields: ["colour", "product_colour", "color"] },
  // `account_id` is the last resort, not a peer: a customer search returns
  // rows that carry nothing but the account, and a row with no identity
  // column at all cannot be chosen from.
  { label: "Customer", fields: ["customer_name", "company_name", "ship_to_name", "account_id"] },
  { label: "Qty", fields: ["ordered_quantity", "quantity", "confirmed_quantity"] },
];

/** The first alias this row actually carries, or null. */
function primaryField(row: Record<string, unknown>, fields: readonly string[]): string | null {
  for (const field of fields) {
    const value = row[field];
    if (typeof value === "string" && value.trim() !== "") return field;
    if (typeof value === "number" || typeof value === "boolean") return field;
  }
  return null;
}

/** The value behind `primaryField`, or null. */
function primaryValue(row: Record<string, unknown>, fields: readonly string[]): string | null {
  const field = primaryField(row, fields);
  if (field === null) return null;
  const value = row[field];
  return typeof value === "string" ? value : String(value);
}

/** As `scalar`: the absence marker is the accessor's, never a JSX fallback. */
function primaryDisplay(row: Record<string, unknown>, fields: readonly string[]): string {
  const value = primaryValue(row, fields);
  if (value === null) return "-";
  return value;
}

export function CandidateOrderMode({
  candidates,
  totalFound = null,
  returnHistory,
  returnHistoryPending,
  returnHistoryError,
  onSelectCandidate,
  canSelectCandidate,
  configuredColumns = [],
}: CandidateOrderModeProps) {
  const [visible, setVisible] = useState<number>(ROW_PAGE);
  const [expanded, setExpanded] = useState<ReadonlySet<number>>(new Set());

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

  // The operator's columns when the release states them, the built-in
  // identity columns when it does not.
  const primaryColumns = configuredColumns.length > 0 ? configuredColumns : PRIMARY_COLUMNS;
  // Only the identity columns the page's rows actually carry.
  const activeColumns = primaryColumns.filter((column) =>
    shown.some((row) => primaryValue(row, column.fields) !== null),
  );
  /**
   * The fields a column actually drew *for this row*, which is not the same as
   * the fields it could have drawn.
   *
   * Built per row rather than from every alias of every active column, and that
   * is the fix for a real disappearance: `account_id` is the Customer column's
   * last-resort alias, so on a customer search -- where `customer_name` is what
   * renders -- the account was treated as already shown and filtered out of the
   * details too. It appeared nowhere, and the branch is exactly what tells five
   * customers of the same name apart.
   */
  const drawnFields = (row: Record<string, unknown>): Set<string> => {
    const drawn = new Set<string>();
    for (const column of activeColumns) {
      const field = primaryField(row, column.fields);
      if (field !== null) drawn.add(field);
    }
    return drawn;
  };
  const detailFields = (row: Record<string, unknown>): [string, unknown][] => {
    const drawn = drawnFields(row);
    return Object.entries(row).filter(
      ([field]) => !SUPPRESSED_COLUMNS.has(field) && !drawn.has(field),
    );
  };

  const toggle = (index: number) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

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
              {activeColumns.map((column) => (
                <th key={column.label} className="px-3 py-2 font-medium">
                  {column.label}
                </th>
              ))}
              <th className="px-3 py-2 font-medium">Details</th>
              <th className="px-3 py-2 text-right font-medium">Action</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((row, index) => {
              const details = detailFields(row);
              const isOpen = expanded.has(index);
              return (
                <Fragment key={index}>
                  <tr className="border-t border-outline-variant/10 transition hover:bg-surface-container-low/60">
                    {activeColumns.map((column) => (
                      <td key={column.label} className="px-3 py-2.5 font-medium text-on-surface">
                        {primaryDisplay(row, column.fields)}
                      </td>
                    ))}
                    <td className="px-3 py-2.5">
                      {details.length > 0 ? (
                        <button
                          type="button"
                          aria-expanded={isOpen}
                          onClick={() => { toggle(index); }}
                          className="inline-flex items-center gap-1 text-xs font-medium text-on-surface-variant transition hover:text-primary"
                        >
                          <ChevronDown
                            size={12}
                            aria-hidden="true"
                            className={`transition-transform ${isOpen ? "rotate-180" : ""}`}
                          />
                          {isOpen ? "Hide" : "More"}
                        </button>
                      ) : (
                        <span className="text-outline">-</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-right">
                      {(() => {
                        const selectable = canSelectCandidate?.(row) ?? true;
                        return (
                          <button
                            type="button"
                            disabled={!selectable}
                            title={
                              selectable
                                ? undefined
                                : "This row names no order or customer the agent can confirm -- say which one in the chat instead."
                            }
                            onClick={() => onSelectCandidate?.(row)}
                            className="inline-flex items-center gap-1 rounded-lg bg-primary-container px-2.5 py-1 text-xs font-semibold text-white shadow-xs transition hover:bg-primary-container/90 disabled:cursor-not-allowed disabled:bg-outline-variant disabled:text-on-surface-variant disabled:shadow-none"
                          >
                            <span>Select</span>
                            <ChevronRight size={12} />
                          </button>
                        );
                      })()}
                    </td>
                  </tr>
                  {isOpen ? (
                    <tr className="border-t border-outline-variant/10 bg-surface-container-low/40">
                      <td colSpan={activeColumns.length + 2} className="px-3 py-3">
                        <dl className="grid grid-cols-2 gap-x-6 gap-y-1.5 md:grid-cols-3">
                          {details.map(([field, value]) => (
                            <div key={field} className="min-w-0">
                              <dt className="text-[10px] uppercase tracking-wide text-outline">
                                {field.replace(/_/g, " ")}
                              </dt>
                              <dd className="truncate text-xs text-on-surface" title={scalar(value)}>
                                {scalar(value)}
                              </dd>
                            </div>
                          ))}
                        </dl>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      {hiddenOnPage > 0 ? (
        <button
          type="button"
          onClick={() => {
            setVisible((count) => count + ROW_PAGE);
          }}
          className="self-start rounded-lg border border-outline-control bg-surface px-3 py-1.5 text-xs font-semibold text-on-surface"
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

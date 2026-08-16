/**
 * The confirmed order's lines, and the selection that holds quantity on them.
 *
 * ```text
 * GET  /api/cases/{caseId}/order-lines
 * POST /api/cases/{caseId}/selected-items
 * ```
 *
 * **Case-bound, and there is deliberately no order-scoped read.** A naked order
 * reference is not an authorization boundary; the case records which order the
 * associate confirmed, under which tenant and which principal, and both routes
 * resolve the order from it. Somebody else's case answers 404.
 *
 * **The write is replace-set.** The payload is the whole selection, so removing
 * a line means sending the remaining ones -- the server releases the withdrawn
 * line's hold in the same transaction. Re-sending an identical selection
 * changes nothing and answers `changed: false` with the revision unmoved.
 *
 * Two refusals the caller must be able to tell apart, both already explained by
 * the backend's `detail.message`, which `APIError` surfaces verbatim:
 * `422 SELECTION_TERM_NOT_PUBLISHED` names a reason or condition the active
 * release does not publish, and `409 QUANTITY_UNAVAILABLE` carries the
 * quantities recomputed inside the transaction that refused. Neither is
 * retryable as sent.
 */

import type { components } from "./generated/return-platform";
import { apiClient } from "./client";

/** `Served<T>`: FastAPI serializes every field, so nullable never means absent. */
type Served<T> = T extends readonly (infer Element)[]
  ? readonly Served<Element>[]
  : T extends object
    ? { [K in keyof T]-?: Served<Exclude<T[K], undefined>> }
    : T;

/**
 * One line of the confirmed order, with the arithmetic behind its availability.
 *
 * The four terms travel with the answer on purpose: a pane showing only
 * `returnableQuantity` cannot tell an associate why a line of eight offers two,
 * and "six came back already" is a different conversation from "six are held by
 * another counter right now".
 *
 * `unitPrice` is a decimal string and is **absent** when the source carries no
 * price -- never `"0.00"`. Nothing in the copilot renders it: the case carries
 * no order total, so any figure derived from one line would be a figure with no
 * source.
 */
export type OrderLineView = Served<components["schemas"]["OrderLineView"]>;

export type OrderLines = Served<components["schemas"]["OrderLines"]>;

/** What the case now holds, plus the refreshed lines, in one answer. */
export type SelectedItems = Served<components["schemas"]["SelectedItems"]>;

/**
 * One line the associate is naming.
 *
 * No product reference and no price: both are properties of the order line and
 * are resolved server-side from the source. A client able to state them could
 * record a return against a product the order does not contain.
 */
export type SelectedItemRequest = {
  readonly orderLineReference: string;
  readonly quantity: number;
  readonly reason?: string | null;
  readonly condition?: string | null;
  readonly packageReference?: string | null;
};

/**
 * The branch associate a carrier can reach about this return.
 *
 * **Case-level, and a sibling of `items` rather than a field on one.** One
 * associate raises one return, so a contact per line would let a two-line
 * selection carry two different people. It is stored on the case fact log --
 * `branch_associate_name`, `branch_associate_email`, `branch_associate_phone`
 * -- which is where every other case-level return detail already lives, and is
 * read back off `CaseProjection.facts`.
 *
 * All three are **optional** by operator instruction, exactly as the branch
 * number is: Fergusonhome's own list marks them so, and a return with none
 * still goes. None of them may ever acquire a default.
 *
 * The empty string is a retraction, not a null. The fact log is append-only, so
 * clearing a value the associate entered by mistake appends an empty one over
 * it; omitting the field entirely says this submission makes no claim about it.
 */
export type ReturnContactRequest = {
  readonly name: string;
  readonly email: string;
  readonly phone: string;
};

/**
 * How much of a line this case may still take.
 *
 * The case's own hold is added back because the write replaces the selection it
 * is editing: an associate raising a quantity from 1 to 2 must not have to
 * out-bid the hold they already own. This is the same subtraction the backend
 * performs, and it is a display bound rather than a promise -- the write
 * re-evaluates inside its transaction and can still refuse.
 */
export function selectableQuantity(line: OrderLineView): number {
  return line.returnableQuantity + line.selfReservedQuantity;
}

export const orderLinesApi = {
  /** The lines of the order this case confirmed, with what is still returnable. */
  async read(caseId: string): Promise<OrderLines> {
    const response = await apiClient<OrderLines>(
      `/api/cases/${encodeURIComponent(caseId)}/order-lines`,
    );
    if (!response.data) throw new Error("The order's lines could not be read.");
    return response.data;
  },

  /**
   * Record the whole selection and hold the quantity, or be refused.
   *
   * `contact` is omitted from the body when `null`, which is the difference
   * between "the associate did not touch the contact fields" and "the associate
   * cleared them". Sending `{name: "", ...}` retracts what the case holds;
   * sending nothing leaves it standing.
   */
  async replaceSelection(
    caseId: string,
    items: readonly SelectedItemRequest[],
    contact: ReturnContactRequest | null = null,
  ): Promise<SelectedItems> {
    const response = await apiClient<SelectedItems>(
      `/api/cases/${encodeURIComponent(caseId)}/selected-items`,
      {
        method: "POST",
        // `createHeaders` sets Accept but not Content-Type, so every JSON POST
        // in this codebase declares its own.
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(contact === null ? { items } : { items, contact }),
      },
    );
    if (!response.data) throw new Error("The selection was not recorded.");
    return response.data;
  },
};

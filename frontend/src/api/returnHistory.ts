import { apiClient } from "./client";

/**
 * Returns the graph knows about, anchored on an order or on a customer.
 *
 * Distinct from `casesApi` on purpose. `/api/cases` is "show me *this* case",
 * addressed by case id and scoped to the caller's own. This is the question the
 * associate actually asks at the counter -- *has this customer returned this
 * before?* -- whose answer includes cases somebody else raised, in a
 * conversation this one knows nothing about.
 *
 * That answer is a graph traversal (customer -> orders -> cases -> RMAs ->
 * items), not a document read, which is why it has its own endpoint rather than
 * a wider `casesApi.list`.
 */

/** Where one parcel or pallet physically is. */
export type ReturnHistoryPlacement = {
  handlingUnitId: string;
  handlingUnitType: string | null;
  physicalStatus: string | null;
  warehouseId: string | null;
  bayId: string | null;
  trackingNumber: string | null;
};

export type ReturnHistoryItem = {
  returnItemId: string;
  orderLineReference: string | null;
  productReference: string | null;
  quantity: number | null;
  reason: string | null;
};

/** One RMA with the items it covers. One RMA covers many items -- decision D6. */
export type ReturnHistoryRecord = {
  returnRecordId: string;
  returnReference: string | null;
  status: string | null;
  returnLocation: string | null;
  trackingReference: string | null;
  items: ReturnHistoryItem[];
};

export type ReturnHistoryCase = {
  caseId: string;
  status: string | null;
  confirmedOrderReference: string | null;
  createdAt: string | null;
  returnRecords: ReturnHistoryRecord[];
  /** Lines named before any RMA covers them. Never folded into the first record. */
  unassignedItems: ReturnHistoryItem[];
  placements: ReturnHistoryPlacement[];
};

export type ReturnHistory = {
  orderReference: string | null;
  accountId: string | null;
  customerId: string | null;
  cases: ReturnHistoryCase[];
};

const EMPTY: ReturnHistory = {
  orderReference: null,
  accountId: null,
  customerId: null,
  cases: [],
};

export const returnHistoryApi = {
  /** Every return raised against this order number, by anyone. */
  async byOrder(orderReference: string): Promise<ReturnHistory> {
    const response = await apiClient<ReturnHistory>(
      `/api/return-history?orderReference=${encodeURIComponent(orderReference)}`,
    );
    return response.data ?? EMPTY;
  },

  /**
   * Every return this customer has raised, across all their orders.
   *
   * Both parts of the key, always: an ERP customer number is unique within a
   * branch account and not across them, so a lookup on the number alone would
   * answer for somebody else's customer.
   */
  async byCustomer(accountId: string, customerId: string): Promise<ReturnHistory> {
    const query = new URLSearchParams({ accountId, customerId });
    const response = await apiClient<ReturnHistory>(`/api/return-history?${query.toString()}`);
    return response.data ?? EMPTY;
  },
};

import { apiClient } from "./client";

/**
 * The Shipment Status Console's surface.
 *
 * Everything the console renders is served: the catalog (codes, labels,
 * transitions, colour tokens) is the release's `shipment_tracking` block, and
 * the documents come back under whatever field names that block maps. Nothing
 * here declares a status code or a field constant — a code appearing in this
 * file would be the defect the catalog exists to prevent, so the document type
 * is an open record and readers go through the accessors below.
 */

export type CatalogStatus = {
  code: string;
  label: string;
  ladder: string;
  ordinal: number;
  terminal: boolean;
  exception_state: boolean;
  color_token: string;
  allowed_next: string[];
};

export type ShipmentStatusCatalog = {
  statuses: CatalogStatus[];
  initialStatusParcel: string;
  initialStatusFreight: string;
  freightMethods: string[];
};

/** Field names are release-mapped, so a document is an open record. */
export type ShipmentDocument = Readonly<Record<string, unknown>>;

/**
 * Read a logical field off a document that may store it under a mapped name.
 * The candidates are the logical name plus any aliases the deployment's field
 * map has used; the first present wins. Returns null rather than inventing.
 */
export function field(document: ShipmentDocument, logical: string): unknown {
  if (logical in document) return document[logical];
  return null;
}

export function text(document: ShipmentDocument, logical: string): string | null {
  const value = field(document, logical);
  if (typeof value === "string" && value.trim() !== "") return value;
  if (typeof value === "number") return String(value);
  return null;
}

export type ShipmentEventInput = {
  status: string;
  location?: string;
  note?: string;
  eventAt?: string;
  override?: boolean;
  overrideReason?: string;
};

async function unwrap<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiClient<T>(path, init);
  if (response.data === null || response.data === undefined) {
    throw new Error(`The server returned no data for ${path}.`);
  }
  return response.data;
}

export const shipmentsApi = {
  catalog: () => unwrap<ShipmentStatusCatalog>("/api/shipment-status-catalog"),

  list: (filters: { status?: string; case?: string; search?: string } = {}) => {
    const params = new URLSearchParams();
    if (filters.status) params.set("status", filters.status);
    if (filters.case) params.set("case", filters.case);
    if (filters.search) params.set("search", filters.search);
    const query = params.toString();
    return unwrap<ShipmentDocument[]>(`/api/shipments${query ? `?${query}` : ""}`);
  },

  get: (identifier: string) =>
    unwrap<ShipmentDocument>(`/api/shipments/${encodeURIComponent(identifier)}`),

  appendEvent: (shipmentId: string, event: ShipmentEventInput) =>
    unwrap<ShipmentDocument>(`/api/shipments/${encodeURIComponent(shipmentId)}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(event),
    }),
};

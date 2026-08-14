import { apiClient, APIError } from "./client";

/**
 * `POST /api/return-shipments/{return_reference}/updates` -- one carrier
 * observation of one return parcel.
 *
 * **RMA-scoped, not case-scoped.** The path segment is the RMA, because
 * `dbo.return_tracking` is keyed on it: a case with three RMAs has three
 * independent shipments, and there is no endpoint that could update "the case's
 * tracking" because there is no such column to update.
 *
 * **Three outcomes, all of them 200.** `APPLIED`, `DUPLICATE` and `STALE` are
 * the store's verdicts on a well-formed request, settled inside the UPDATE's
 * own WHERE clause under `UPDLOCK, HOLDLOCK`. A caller replaying a carrier feed
 * has to be able to tell "already knew that" from "your request was wrong", so
 * none of the three is an error and the console renders none of them as one.
 */

/**
 * `CK_return_tracking_type`'s vocabulary, in the order an operator meets it.
 *
 * Required on every update rather than defaulted: a shipment's ship-via is a
 * property of that shipment, and defaulting would file a BOL freight movement
 * as a parcel with nothing downstream able to tell.
 */
export const TRACKING_TYPES = [
  "PPL",
  "BOL",
  "CUSTOMER_SHIP",
  "NO_LABEL",
  "DIRECT_VENDOR",
  "FIELD_SCRAP",
] as const;

export type TrackingType = (typeof TRACKING_TYPES)[number];

/** What the store decided. Every one of these arrives with a 200. */
export type ShipmentUpdateOutcome = "APPLIED" | "DUPLICATE" | "STALE";

export type ShipmentUpdateInput = {
  readonly trackingReference: string;
  readonly shipmentStatus: string;
  /**
   * The carrier's status timestamp, and the ordering authority for the whole
   * contract: `APPLIED` vs `STALE` is decided against this and nothing else.
   * **Must carry a timezone offset** -- the backend refuses a naive one rather
   * than reading it as UTC, because that would let a submitter in another zone
   * silently overtake an event it has no relationship to.
   */
  readonly statusAt: string;
  readonly trackingType: TrackingType;
  readonly carrierCode?: string;
  readonly shipmentDetails?: string;
};

/** What the graph said about the parcel, and who was told. */
export type ShipmentReading = {
  readonly caseId: string | null;
  readonly fulfillmentStatus: string;
  readonly evidence: string;
  readonly evidenceReference: string;
  readonly graphGenerationId: string | null;
  readonly observedStatus: string | null;
};

export type ShipmentUpdateResult = {
  readonly outcome: ShipmentUpdateOutcome;
  readonly returnReference: string;
  readonly trackingReference: string;
  readonly currentStatus: string;
  readonly currentStatusAt: string;
  readonly rowVersion: number;
  readonly graphGenerationId: string | null;
  /**
   * Present only for `APPLIED`. A duplicate submission of a status the
   * associate has already been shown appends no second fact, and a stale one
   * appends none at all, so neither produces a reading -- absent here means
   * "nothing new was told to anyone", not "the reading failed".
   */
  readonly reading: ShipmentReading | null;
};

/**
 * The SQL row committed and the graph projection did not.
 *
 * Kept as its own type because the operator's next move is the opposite of the
 * one a normal failure calls for: the authoritative write already happened, so
 * resubmitting the identical update is safe and answers `DUPLICATE`. Telling
 * someone "it failed" here invites them to re-enter it as a *new* event with a
 * fresh timestamp, which is how a retry turns into a second history.
 */
export class ShipmentGraphSyncFailed extends Error {
  public readonly retryable = true;

  public constructor(message: string) {
    super(message);
    this.name = "ShipmentGraphSyncFailed";
  }
}

/** ISO 8601 with the browser's own offset, which is what `statusAt` requires. */
export function nowWithOffset(at: Date = new Date()): string {
  const offset = -at.getTimezoneOffset();
  const sign = offset < 0 ? "-" : "+";
  const pad = (value: number) => String(Math.floor(Math.abs(value))).padStart(2, "0");
  return (
    `${String(at.getFullYear())}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}`
    + `T${pad(at.getHours())}:${pad(at.getMinutes())}:${pad(at.getSeconds())}`
    + `${sign}${pad(offset / 60)}:${pad(offset % 60)}`
  );
}

export const returnShipmentsApi = {
  async recordUpdate(
    returnReference: string,
    input: ShipmentUpdateInput,
  ): Promise<ShipmentUpdateResult> {
    try {
      const response = await apiClient<ShipmentUpdateResult>(
        `/api/return-shipments/${encodeURIComponent(returnReference)}/updates`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(input),
        },
      );
      if (!response.data) throw new Error("The shipment update returned no result.");
      return response.data;
    } catch (error) {
      // 502 is the one status on this route that does not mean "nothing
      // happened". Recognised by status rather than by a `code` field because
      // `apiClient` keeps the detail's message and drops its code, and the
      // route raises no other 502.
      if (error instanceof APIError && error.status === 502) {
        throw new ShipmentGraphSyncFailed(error.message);
      }
      throw error;
    }
  },
};

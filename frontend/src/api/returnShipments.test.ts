/**
 * The shipment client, against real response bodies.
 *
 * The one behaviour worth pinning is the 502: it is the only status on this
 * route that does not mean "nothing happened", and treating it like an ordinary
 * failure invites the operator to re-enter a committed observation with a fresh
 * timestamp -- which turns a retry into a second history.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  nowWithOffset,
  returnShipmentsApi,
  ShipmentGraphSyncFailed,
  TRACKING_TYPES,
} from "./returnShipments";
import { APIError } from "./client";

function envelope(data: unknown) {
  return JSON.stringify({
    data,
    page: null,
    meta: {
      schema_version: "1.0",
      request_id: "test",
      generated_at: "2026-08-14T00:00:00Z",
      freshness: "LIVE",
      partial: false,
      warnings: [],
    },
  });
}

function respond(body: string, status = 200): Response {
  return new Response(body, { status, headers: { "Content-Type": "application/json" } });
}

const APPLIED = {
  outcome: "APPLIED",
  returnReference: "RMA-1",
  trackingReference: "1Z-A",
  currentStatus: "IN_TRANSIT",
  currentStatusAt: "2026-08-12T09:00:00Z",
  rowVersion: 4,
  graphGenerationId: "gen-7",
  reading: null,
};

describe("returnShipmentsApi", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("posts to the RMA-scoped path", async () => {
    vi.mocked(fetch).mockResolvedValue(respond(envelope(APPLIED)));

    await returnShipmentsApi.recordUpdate("RMA/1 2", {
      trackingReference: "1Z-A",
      shipmentStatus: "IN_TRANSIT",
      statusAt: "2026-08-12T09:00:00+05:30",
      trackingType: "BOL",
    });

    const [path, init] = vi.mocked(fetch).mock.calls[0] as [string, { body: string }];
    // Encoded, because an RMA reference is customer data and a slash in one
    // would otherwise address a different route entirely.
    expect(path).toBe("/api/return-shipments/RMA%2F1%202/updates");
    expect(JSON.parse(init.body)).toEqual({
      trackingReference: "1Z-A",
      shipmentStatus: "IN_TRANSIT",
      statusAt: "2026-08-12T09:00:00+05:30",
      trackingType: "BOL",
    });
  });

  it.each(["APPLIED", "DUPLICATE", "STALE"])("returns %s as a result, not a throw", async (
    outcome,
  ) => {
    vi.mocked(fetch).mockResolvedValue(respond(envelope({ ...APPLIED, outcome })));

    const result = await returnShipmentsApi.recordUpdate("RMA-1", {
      trackingReference: "1Z-A",
      shipmentStatus: "IN_TRANSIT",
      statusAt: "2026-08-12T09:00:00+00:00",
      trackingType: "PPL",
    });

    expect(result.outcome).toBe(outcome);
  });

  it("maps the 502 to a retry-safe error carrying the server's explanation", async () => {
    vi.mocked(fetch).mockResolvedValue(
      respond(
        JSON.stringify({
          detail: {
            code: "SHIPMENT_GRAPH_SYNC_FAILED",
            message:
              "The shipment update was committed to the authoritative store and could not be "
              + "projected into the graph. Resubmitting the identical update is safe and will "
              + "answer DUPLICATE.",
            retryable: true,
          },
        }),
        502,
      ),
    );

    await expect(
      returnShipmentsApi.recordUpdate("RMA-1", {
        trackingReference: "1Z-A",
        shipmentStatus: "IN_TRANSIT",
        statusAt: "2026-08-12T09:00:00+00:00",
        trackingType: "PPL",
      }),
    ).rejects.toThrow(ShipmentGraphSyncFailed);
  });

  it("leaves every other failure as an ordinary APIError", async () => {
    vi.mocked(fetch).mockResolvedValue(
      respond(JSON.stringify({ detail: "Insufficient permissions" }), 403),
    );

    const failure = await returnShipmentsApi
      .recordUpdate("RMA-1", {
        trackingReference: "1Z-A",
        shipmentStatus: "IN_TRANSIT",
        statusAt: "2026-08-12T09:00:00+00:00",
        trackingType: "PPL",
      })
      .catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(APIError);
    expect(failure).not.toBeInstanceOf(ShipmentGraphSyncFailed);
  });
});

describe("nowWithOffset", () => {
  it("always carries an offset, which is what the backend requires", () => {
    // A naive timestamp is refused rather than read as UTC, because reading it
    // as UTC would let a submitter in another zone silently overtake an event.
    expect(nowWithOffset()).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$/);
  });
});

describe("TRACKING_TYPES", () => {
  it("is the CK_return_tracking_type vocabulary and nothing else", () => {
    expect([...TRACKING_TYPES]).toEqual([
      "PPL",
      "BOL",
      "CUSTOMER_SHIP",
      "NO_LABEL",
      "DIRECT_VENDOR",
      "FIELD_SCRAP",
    ]);
  });
});

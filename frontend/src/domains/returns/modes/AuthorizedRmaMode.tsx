import {
  Barcode,
  CheckCircle2,
  MapPin,
  Printer,
  Truck,
} from "lucide-react";
import type { ReturnArtifactProjection, ReturnRecordProjection } from "../../../api/cases";
import { activeArtifacts, activeArtifactsForShipment, activeShipments } from "../types";

/**
 * The authorized RMA, exactly as the case projection has it.
 *
 * Every value on this pane used to have a `??` literal behind it -- an RMA
 * number, a tracking number, a carrier that was really the order's source
 * system, a destination facility nobody had chosen. They are gone, and what
 * replaces each of them is either the platform's value or the word `Pending`.
 *
 * **The label action resolves the active artifact, never `artifacts[0]`.** A
 * superseded label is retained for audit, and reprinting a replaced one sends
 * the parcel to the address it was replaced for.
 */

export type AuthorizedRmaModeProps = {
  returnRecords?: readonly ReturnRecordProjection[];
  /** Retrieves the document. Absent while no case-scoped label route exists. */
  onPrintLabel?: (artifact: ReturnArtifactProjection) => void;
};

const PENDING = "Pending";

/** Carrier and service as one line, or `Pending`. Never the order's source system. */
function carrierAndService(carrier: string | null, serviceLevel: string | null): string {
  if (carrier === null && serviceLevel === null) return PENDING;
  return [carrier, serviceLevel].filter((part) => part !== null).join(" · ");
}

export function AuthorizedRmaMode({
  returnRecords = [],
  onPrintLabel,
}: AuthorizedRmaModeProps) {
  // The RMA this pane is about. One case can carry several, and the ones after
  // the first are rendered as their own routing blocks below rather than
  // silently dropped -- picking one and hiding the rest is what sent half a
  // shipment to the wrong dock.
  const record = returnRecords.at(0) ?? null;
  const rmaNumber = record?.returnReference ?? PENDING;
  const status = record?.status ?? PENDING;
  const destination = record?.returnLocation ?? PENDING;
  const method = record?.returnMethod ?? PENDING;
  const shipments = record === null ? [] : activeShipments(record);
  const labels = record === null ? [] : activeArtifacts(record, "SHIPPING_LABEL");
  // The single action's artifact. With several packages the associate is
  // holding one of them, and each package's own label is named on its row.
  const printable = labels.at(0) ?? null;

  return (
    <div className="flex flex-col gap-4">
      {/* 1. RMA Status Banner */}
      <div className="flex flex-col gap-2 rounded-xl border border-emerald-500/30 bg-emerald-50/50 p-4 shadow-xs">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span className="flex size-8 items-center justify-center rounded-full bg-emerald-600 text-white shadow-xs">
              <CheckCircle2 size={18} />
            </span>
            <div>
              <span className="text-[11px] font-bold uppercase tracking-wider text-emerald-800">
                Return Authorized · External System
              </span>
              <h3 className="text-base font-mono font-bold text-on-surface">
                {rmaNumber}
              </h3>
            </div>
          </div>
          <span className="rounded-full bg-emerald-100 px-3 py-1 text-xs font-bold text-emerald-800 uppercase tracking-wider">
            {status}
          </span>
        </div>
      </div>

      {/* 2. Routing, Carrier, Tracking & Destination Card */}
      <div className="flex flex-col gap-3 rounded-xl border border-outline-variant/30 bg-surface-container-lowest p-4 shadow-xs">
        <span className="text-xs font-semibold uppercase tracking-wider text-outline">
          Logistics & Routing Details
        </span>

        <dl className="flex flex-col gap-2.5 text-xs">
          <div className="flex justify-between border-b border-outline-variant/15 pb-2">
            <dt className="text-outline flex items-center gap-1.5">
              <MapPin size={13} className="text-secondary" />
              <span>Destination Facility</span>
            </dt>
            <dd className="font-bold text-on-surface text-right truncate max-w-[60%]">
              {destination}
            </dd>
          </div>

          <div className="flex justify-between border-b border-outline-variant/15 pb-2">
            <dt className="text-outline">Return Method</dt>
            <dd className="font-semibold text-on-surface">{method}</dd>
          </div>

          {shipments.length === 0 ? (
            <>
              <div className="flex justify-between border-b border-outline-variant/15 pb-2">
                <dt className="text-outline flex items-center gap-1.5">
                  <Truck size={13} className="text-secondary" />
                  <span>Carrier & Service</span>
                </dt>
                <dd className="font-semibold text-on-surface">{PENDING}</dd>
              </div>
              <div className="flex justify-between border-b border-outline-variant/15 pb-2">
                <dt className="text-outline">Tracking Number</dt>
                <dd className="font-mono font-bold text-primary flex items-center gap-1.5">
                  {/* An RMA with a label and no package is an ordinary state,
                      and the only honest thing to say about its tracking is
                      that there is none yet. */}
                  <span>{PENDING}</span>
                </dd>
              </div>
              <div className="flex justify-between border-b border-outline-variant/15 pb-2">
                <dt className="text-outline">Label</dt>
                <dd className="font-semibold text-on-surface truncate max-w-[60%]">
                  {/* The document exists before the package does. That is the
                      whole reason artifacts hang off the record. */}
                  {printable?.fileName ?? printable?.artifactId ?? PENDING}
                </dd>
              </div>
            </>
          ) : (
            shipments.map((shipment) => {
              const shipmentLabels =
                record === null
                  ? []
                  : activeArtifactsForShipment(record, "SHIPPING_LABEL", shipment.shipmentId);
              return (
                <div key={shipment.shipmentId} className="flex flex-col gap-2.5">
                  <div className="flex justify-between border-b border-outline-variant/15 pb-2">
                    <dt className="text-outline flex items-center gap-1.5">
                      <Truck size={13} className="text-secondary" />
                      <span>Carrier & Service · {shipment.shipmentId}</span>
                    </dt>
                    <dd className="font-semibold text-on-surface">
                      {carrierAndService(shipment.carrier, shipment.serviceLevel)}
                    </dd>
                  </div>
                  <div className="flex justify-between border-b border-outline-variant/15 pb-2">
                    <dt className="text-outline">Tracking Number · {shipment.shipmentId}</dt>
                    <dd className="font-mono font-bold text-primary flex items-center gap-1.5">
                      <span>{shipment.trackingNumber ?? PENDING}</span>
                    </dd>
                  </div>
                  <div className="flex justify-between border-b border-outline-variant/15 pb-2">
                    <dt className="text-outline">Label · {shipment.shipmentId}</dt>
                    <dd className="font-semibold text-on-surface truncate max-w-[60%]">
                      {shipmentLabels.at(0)?.fileName ?? shipmentLabels.at(0)?.artifactId ?? PENDING}
                    </dd>
                  </div>
                </div>
              );
            })
          )}
        </dl>
      </div>

      {/* 3. Barcode & Shipping Label Graphic */}
      <div className="flex flex-col items-center justify-center rounded-xl border border-outline-variant/40 bg-surface-container-low/70 py-4 px-6 text-center shadow-xs">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-outline mb-2">
          Master RMA Scan Tag
        </span>
        <div className="rounded-lg bg-white p-3 shadow-xs border border-outline-variant/20 flex flex-col items-center">
          <Barcode size={52} className="text-on-surface" />
          <span className="font-mono text-xs font-bold text-on-surface tracking-widest mt-1">
            *{rmaNumber}*
          </span>
        </div>
        <p className="text-[11px] text-outline mt-2">
          Scannable at warehouse inbound dock and carrier pickup.
        </p>
      </div>

      {/* 4. Action Buttons: Print Label / BOL */}
      <div className="flex flex-col gap-2">
        <button
          type="button"
          // Disabled without a live artifact *and* something that can fetch it.
          // The shipped button called `window.print()`, which prints the web
          // page: an associate handed that instead of a carrier label has a
          // screenshot of a screen, not a document a driver will accept.
          disabled={printable === null || onPrintLabel === undefined}
          onClick={() => {
            if (printable !== null) onPrintLabel?.(printable);
          }}
          className="flex w-full items-center justify-center gap-2 rounded-xl bg-primary-container py-3 text-xs font-bold text-white shadow-sm transition hover:bg-primary-container/90 disabled:opacity-40"
        >
          <Printer size={16} />
          <span>Print Shipping Label & BOL</span>
        </button>

        <p className="text-center text-[11px] text-outline">
          {printable === null
            ? "No label document has been issued for this RMA yet."
            : "Authorized RMA manifest transmitted to warehouse inbound queues."}
        </p>
      </div>

      {/* 5. Further RMAs on the same case, each with its own routing. */}
      {returnRecords.slice(1).map((other) => (
        <div
          key={other.returnRecordId}
          className="flex flex-col gap-2 rounded-xl border border-outline-variant/30 bg-surface-container-lowest p-4 shadow-xs"
        >
          <div className="flex items-baseline justify-between gap-2">
            <span className="font-mono text-xs font-bold text-on-surface">
              {other.returnReference ?? PENDING}
            </span>
            <span className="text-xs font-medium text-primary">{other.status ?? PENDING}</span>
          </div>
          <dl className="flex flex-col gap-2 text-xs">
            <div className="flex justify-between">
              <dt className="text-outline">Destination Facility</dt>
              <dd className="font-semibold text-on-surface">{other.returnLocation ?? PENDING}</dd>
            </div>
            {activeShipments(other).map((shipment) => (
              <div key={shipment.shipmentId} className="flex justify-between">
                <dt className="text-outline">Tracking Number · {shipment.shipmentId}</dt>
                <dd className="font-mono font-semibold text-on-surface">
                  {shipment.trackingNumber ?? PENDING}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      ))}
    </div>
  );
}

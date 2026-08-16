import { CheckCircle2, Circle, Clock, ExternalLink, Truck } from "lucide-react";
import type { ShipmentProjection, ShipmentStatus } from "../../../api/cases";

/**
 * Where the packages are, from the shipment projection.
 *
 * The pane used to draw a four-step milestone chain with invented timestamps
 * and invented locations -- "Atlanta Dispatch Terminal", "Regional Sort Hub" --
 * beside a carrier that was the order's source system and an ETA that was a
 * return-method enum. There is no carrier event feed behind this platform, so
 * there is no honest way to draw a scan history. What replaces it is the
 * platform's own shipment status, rendered as the chain it already is: the
 * steps are the `ShipmentStatus` vocabulary and the position is the status the
 * shipment actually holds.
 */

export type CarrierTransitModeProps = {
  shipments?: readonly ShipmentProjection[];
  onOpenTrackingPortal?: (shipment: ShipmentProjection) => void;
};

const PENDING = "Pending";

/**
 * The chain, in the order a package moves through it.
 *
 * `CANCELLED` is deliberately absent: it is not a step on the way anywhere, and
 * a cancelled package is filtered out before this pane sees it.
 */
const CHAIN: readonly { readonly status: ShipmentStatus; readonly title: string }[] = [
  { status: "AWAITING_HANDOFF", title: "Awaiting handoff" },
  { status: "IN_TRANSIT", title: "In transit" },
  { status: "DELIVERED", title: "Delivered" },
  { status: "RECEIVED", title: "Received" },
];

/** An instant as the reader's own clock reads it, or the raw value if it will not parse. */
function instant(value: string | null): string | null {
  if (value === null) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function ShipmentTransit({
  shipment,
  onOpenTrackingPortal,
}: {
  shipment: ShipmentProjection;
  onOpenTrackingPortal?: (shipment: ShipmentProjection) => void;
}) {
  const reached = CHAIN.findIndex((step) => step.status === shipment.shipmentStatus);
  const carrier = shipment.carrier ?? PENDING;
  const trackingNumber = shipment.trackingNumber ?? PENDING;
  const eta = instant(shipment.estimatedDeliveryAt) ?? PENDING;

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-outline-variant bg-surface-container-low p-4">
      {/* Transit Header */}
      <div className="flex items-center justify-between border-b border-outline-variant pb-3">
        <div>
          <span className="text-xs font-semibold uppercase tracking-wider text-outline">
            Fulfillment & Transit
          </span>
          <h3 className="text-base font-bold text-on-surface flex items-center gap-1.5 mt-0.5">
            <Truck size={18} className="text-primary" />
            <span>{carrier}</span>
          </h3>
        </div>
        <span className="rounded-full bg-secondary-container px-2.5 py-1 text-xs font-semibold text-primary">
          {shipment.shipmentStatus ?? PENDING}
        </span>
      </div>

      {/* Tracking Summary Block */}
      <div className="flex justify-between items-center rounded border border-outline-variant bg-surface-container-lowest p-2.5 text-xs">
        <div>
          <span className="text-outline block">Tracking Number · {shipment.shipmentId}</span>
          <span className="font-mono font-semibold text-on-surface">{trackingNumber}</span>
        </div>
        <div className="text-right">
          <span className="text-outline block">Est. Delivery</span>
          <span className="font-semibold text-primary">{eta}</span>
        </div>
      </div>

      {/* Compact Milestone Chain, drawn from the shipment's own status */}
      <div className="flex flex-col gap-2 pt-1">
        <span className="text-xs font-semibold text-outline">Milestone Chain</span>
        <ol className="flex flex-col gap-0 border-l border-outline-variant ml-2 pl-3">
          {CHAIN.map((step, index) => {
            const isComplete = reached >= 0 && index < reached;
            const isCurrent = index === reached;
            // Only the step the package is actually on can be timed, and the
            // only timestamp the platform holds for it is when the shipment
            // last changed. Every other step is left blank rather than dated.
            const timestamp = isCurrent ? instant(shipment.updatedAt) : null;

            return (
              <li key={step.status} className="relative pb-3 last:pb-0">
                <span
                  className={[
                    "absolute -left-[19px] top-0.5 flex size-3.5 items-center justify-center rounded-full bg-surface",
                    isComplete
                      ? "text-primary"
                      : isCurrent
                        ? "text-primary ring-2 ring-primary/40 ring-offset-1 ring-offset-surface"
                        : "text-outline",
                  ].join(" ")}
                >
                  {isComplete ? (
                    <CheckCircle2 size={14} className="fill-primary text-on-primary" />
                  ) : isCurrent ? (
                    <Circle size={10} className="fill-primary" />
                  ) : (
                    <Circle size={8} />
                  )}
                </span>

                <div className="flex items-baseline justify-between gap-2 text-xs">
                  <span
                    className={`font-semibold ${
                      isCurrent
                        ? "text-primary"
                        : isComplete
                          ? "text-on-surface"
                          : "text-outline"
                    }`}
                  >
                    {step.title}
                    {isCurrent ? " (current)" : ""}
                  </span>
                  {timestamp !== null ? (
                    <span className="text-outline shrink-0 flex items-center gap-1">
                      <Clock size={11} />
                      <span>{timestamp}</span>
                    </span>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      </div>

      {/* External Portal Link */}
      <div className="pt-2 border-t border-outline-variant">
        <button
          type="button"
          // No carrier portal address exists on the contract, so there is
          // nowhere for this to go until something supplies one.
          disabled={onOpenTrackingPortal === undefined || shipment.trackingNumber === null}
          onClick={() => {
            onOpenTrackingPortal?.(shipment);
          }}
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-outline-variant bg-surface-container-lowest py-2 text-xs font-semibold text-on-surface transition hover:bg-surface-container disabled:opacity-40"
        >
          <ExternalLink size={13} />
          <span>Carrier Tracking Portal</span>
        </button>
      </div>
    </div>
  );
}

export function CarrierTransitMode({
  shipments = [],
  onOpenTrackingPortal,
}: CarrierTransitModeProps) {
  if (shipments.length === 0) {
    return (
      <div className="flex flex-col gap-3 rounded-lg border border-outline-variant bg-surface-container-low p-4">
        <span className="text-xs font-semibold uppercase tracking-wider text-outline">
          Fulfillment & Transit
        </span>
        <p className="text-xs text-on-surface-variant">
          No package has been tendered against this return yet.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {shipments.map((shipment) => (
        <ShipmentTransit
          key={shipment.shipmentId}
          shipment={shipment}
          onOpenTrackingPortal={onOpenTrackingPortal}
        />
      ))}
    </div>
  );
}

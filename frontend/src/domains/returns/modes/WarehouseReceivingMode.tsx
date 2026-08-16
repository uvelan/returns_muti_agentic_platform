import { CheckCircle, QrCode, ShieldCheck, Warehouse } from "lucide-react";
import type { WarehouseProjection } from "../../../api/cases";

/**
 * Receiving, bay placement and inspection, as far as the platform knows them.
 *
 * Three fields here have a producer today -- `facilityId`, `bayId` and
 * `bayReason`, all written by `ReturnCaseWorkflow`. The rest are declared on the
 * contract and always null, because the contract is where a producer will land.
 * They render `Pending`; they used to render `Central Distribution Center`,
 * `Bay 14-B`, `SCANNED_AT_DOCK` and `Tier 2 Technical Inspection`, none of which
 * any part of this platform has ever computed.
 *
 * **A case with no bay is a normal state.** Placement is advisory and runs
 * before the goods exist, so "no bay" is the ordinary answer for most of a
 * case's life. `bayReason` is the explanation for it -- `NO_ELIGIBLE_BAY`,
 * `PRE_ARRIVAL_NOT_ALLOWED`, `BAY_PLACEMENT_NOT_CONFIGURED` and their siblings
 * -- and it is **not an error**: a reason with no bay beside it is the platform
 * working.
 */

export type WarehouseReceivingModeProps = {
  warehouse?: WarehouseProjection | null;
  onConfirmDockReceipt?: () => void;
  onRouteToQA?: () => void;
  isProcessing?: boolean;
};

const PENDING = "Pending";

/** A projected enum as a sentence-shaped label. */
function humanized(value: string): string {
  const words = value.replace(/_/g, " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function WarehouseReceivingMode({
  warehouse = null,
  onConfirmDockReceipt,
  onRouteToQA,
  isProcessing,
}: WarehouseReceivingModeProps) {
  const facility = warehouse?.facilityName ?? warehouse?.facilityId ?? PENDING;
  const bay = warehouse?.bayId ?? null;
  const bayExplanation =
    warehouse?.bayReason == null ? PENDING : humanized(warehouse.bayReason);
  const scanStatus = warehouse?.warehouseStatus ?? PENDING;
  const qaRoute = warehouse?.qaStatus ?? PENDING;
  const verification = warehouse?.inspectionStatus ?? warehouse?.condition ?? PENDING;

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-outline-variant bg-surface-container-low p-4">
      {/* Physical Handling Header */}
      <div className="flex items-center justify-between border-b border-outline-variant pb-3">
        <div>
          <span className="text-xs font-semibold uppercase tracking-wider text-outline">
            Warehouse Receiving & Bay
          </span>
          <h3 className="text-sm font-bold text-on-surface flex items-center gap-1.5 mt-0.5">
            <Warehouse size={16} className="text-primary" />
            <span>{facility}</span>
          </h3>
        </div>
        <span className="rounded-full bg-secondary-container px-2.5 py-1 text-xs font-semibold text-primary">
          Dock Handoff
        </span>
      </div>

      {/* Typography & Section Separators (No Nested Card Bloat) */}
      <div className="flex flex-col gap-2.5 text-xs">
        <div className="flex justify-between items-center border-b border-outline-variant/60 pb-2">
          <span className="text-outline">Assigned Receiving Bay</span>
          <span className="text-sm font-bold text-primary">{bay ?? bayExplanation}</span>
        </div>

        <div className="flex justify-between items-center border-b border-outline-variant/60 pb-2">
          <span className="text-outline">Intake Scan Status</span>
          <span className="font-semibold text-on-surface flex items-center gap-1">
            <QrCode size={13} className="text-primary" />
            <span>{scanStatus}</span>
          </span>
        </div>

        <div className="flex justify-between items-center border-b border-outline-variant/60 pb-2">
          <span className="text-outline">QA Routing Channel</span>
          <span className="font-semibold text-on-surface flex items-center gap-1">
            <ShieldCheck size={13} className="text-tertiary" />
            <span>{qaRoute}</span>
          </span>
        </div>

        <div className="flex justify-between items-center pb-1">
          <span className="text-outline">Item Verification</span>
          <span className="font-semibold text-on-surface flex items-center gap-1 text-primary">
            <CheckCircle size={13} />
            <span>{verification}</span>
          </span>
        </div>
      </div>

      {/* Operational Actions */}
      <div className="flex flex-col gap-2 pt-3 border-t border-outline-variant">
        <button
          type="button"
          // No case-scoped receiving write exists, so the button is inert
          // unless a caller supplies one. A control that appears to book goods
          // in and does nothing is the same lie as a fabricated bay.
          disabled={isProcessing === true || onConfirmDockReceipt === undefined}
          onClick={() => {
            onConfirmDockReceipt?.();
          }}
          className="w-full rounded-lg bg-primary py-2.5 text-xs font-semibold text-on-primary transition hover:bg-primary-container disabled:opacity-40"
        >
          {isProcessing === true ? "Recording Dock Receipt..." : "Confirm Dock Physical Receipt"}
        </button>

        <button
          type="button"
          disabled={onRouteToQA === undefined}
          onClick={() => {
            onRouteToQA?.();
          }}
          className="w-full rounded-lg border border-outline-variant bg-surface-container-lowest py-2.5 text-xs font-semibold text-on-surface transition hover:bg-surface-container disabled:opacity-40"
        >
          Route to QA Testing Disposition
        </button>
      </div>
    </div>
  );
}

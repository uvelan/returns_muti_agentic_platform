/**
 * What the Copilot is allowed to believe, and how it decides which pane to draw.
 *
 * **Everything here reads `CaseProjection` and nothing else.** The screen used
 * to derive its lifecycle from a `ReturnSessionView` that does not exist for a
 * Copilot return (all eight live cases carry `sessionId: null`), from
 * `candidates.length`, and from `turn.response.status` -- a model answer treated
 * as a workflow transition. All three are gone. `stage` is a pure projection
 * over persisted state with frozen precedence and monotonicity tests behind it,
 * and it is the only thing that moves this screen.
 */

import type {
  CaseProjection,
  CopilotStage,
  ReturnArtifactProjection,
  ReturnArtifactType,
  ReturnRecordProjection,
  ShipmentProjection,
} from "../../api/cases";

/**
 * The eight panes. Fewer than the twelve backend stages, deliberately: the
 * stages are the platform's lifecycle and these are the things an associate
 * looks at, and several stages are answered by the same pane.
 */
export type CopilotLifecycleMode =
  | "DISCOVERY"
  | "CANDIDATE_ORDER"
  | "ITEM_SELECTION"
  | "RETURN_EVALUATION"
  | "AUTHORIZED_RMA"
  | "CARRIER_TRANSIT"
  | "WAREHOUSE_RECEIVING"
  | "RETURN_SETTLEMENT";

/**
 * Which pane answers which stage. Total over `CopilotStage`, so a stage the
 * backend adds is a compile error here rather than a blank screen there.
 *
 * Three stages share `RETURN_EVALUATION`, and that is the honest grouping:
 * `POLICY_EVALUATION` is the evaluator running, `APPROVAL_REQUIRED` is a
 * supervisor holding its answer, and `AWAITING_SUPPORT` is Support verifying a
 * warranty or delivery claim -- all three are the same question ("may this come
 * back, and on whose authority") and the evaluation pane is where it is
 * answered. `RETURN_FACTS` shares `ITEM_SELECTION` because reason and condition
 * are captured against the lines, on that pane.
 *
 * **`ORDER_CONFIRMATION` maps to the item-selection pane, and used to map to
 * the candidate pane on the reading that the associate was still choosing an
 * order.** That reading was wrong, and it deadlocked the capture flow. A case
 * exists only once an order has been confirmed, and `derive_copilot_stage`
 * reaches `ORDER_CONFIRMATION` precisely when the case has a confirmed order
 * and nothing selected against it -- which is the moment the associate has to
 * name the lines, quantities and reason. Sending them to a one-row candidate
 * table instead left the selection pane reachable only *after* a selection
 * existed, and the only screen that can make one is the selection pane.
 *
 * The candidate pane keeps the job it is actually for: choosing among search
 * results before there is a case, which `deriveCopilotMode` routes below
 * without consulting this table at all.
 */
const MODE_FOR_STAGE: Record<CopilotStage, CopilotLifecycleMode> = {
  DISCOVERY: "DISCOVERY",
  ORDER_CONFIRMATION: "ITEM_SELECTION",
  ITEM_SELECTION: "ITEM_SELECTION",
  RETURN_FACTS: "ITEM_SELECTION",
  POLICY_EVALUATION: "RETURN_EVALUATION",
  APPROVAL_REQUIRED: "RETURN_EVALUATION",
  AWAITING_SUPPORT: "RETURN_EVALUATION",
  AUTHORIZED_RMA: "AUTHORIZED_RMA",
  CARRIER_TRANSIT: "CARRIER_TRANSIT",
  WAREHOUSE_RECEIVING: "WAREHOUSE_RECEIVING",
  RETURN_SETTLEMENT: "RETURN_SETTLEMENT",
  COMPLETED: "RETURN_SETTLEMENT",
};

export type CopilotModeContext = {
  /** `caseLifecycle(caseRead)?.stage`. `null` before any case exists. */
  readonly stage: CopilotStage | null;
  /** Search results the associate is still looking at. Never a lifecycle signal. */
  readonly candidates: readonly Record<string, unknown>[];
};

/**
 * The pane to draw.
 *
 * **`stage` decides it whenever there is one.** Candidates are consulted in
 * exactly one situation -- no case at all, or a case the platform still calls
 * `DISCOVERY` -- because a search result is a thing on the associate's screen
 * rather than a thing that happened to the return. Everywhere else a candidate
 * list cannot advance, hold back or reorder the lifecycle.
 */
export function deriveCopilotMode(context: CopilotModeContext): CopilotLifecycleMode {
  const { stage, candidates } = context;
  if (stage === null || stage === "DISCOVERY") {
    return candidates.length > 0 ? "CANDIDATE_ORDER" : "DISCOVERY";
  }
  return MODE_FOR_STAGE[stage];
}

/* -------------------------------------------------------------------------
 * Reading a return record. The contract's own rules, in TypeScript.
 * ---------------------------------------------------------------------- */

/**
 * Whether this is the document the single label action resolves to.
 *
 * Both clauses, and `active` unset is not active: an artifact the platform has
 * not marked live is not evidence that a label exists. **Never `artifacts[0]`**
 * -- a superseded label is kept for audit and reprinting the replaced one sends
 * a parcel to the address it was replaced for.
 */
export function isActiveArtifact(artifact: ReturnArtifactProjection): boolean {
  return artifact.active === true && artifact.supersededBy === null;
}

/** The live documents of one type on this RMA, whatever they are attributed to. */
export function activeArtifacts(
  record: ReturnRecordProjection,
  artifactType: ReturnArtifactType,
): readonly ReturnArtifactProjection[] {
  return (record.artifacts ?? []).filter(
    (artifact) => artifact.artifactType === artifactType && isActiveArtifact(artifact),
  );
}

/**
 * The live documents of one type carried by one named package.
 *
 * Attribution is `shipmentId` and nothing else. An artifact with no
 * `shipmentId` is not on *this* package -- it is on the RMA and no package yet
 * -- so it never papers a parcel that has nothing on it.
 */
export function activeArtifactsForShipment(
  record: ReturnRecordProjection,
  artifactType: ReturnArtifactType,
  shipmentId: string,
): readonly ReturnArtifactProjection[] {
  return activeArtifacts(record, artifactType).filter(
    (artifact) => artifact.shipmentId === shipmentId,
  );
}

/**
 * The one label the print action means, or `null`.
 *
 * A label with no package is a real and ordinary shape -- record `4e372a39…`
 * carries exactly that -- so an unattributed active label counts. Where several
 * are live at once the RMA has several packages, and the caller that knows
 * which package it is holding asks `activeArtifactsForShipment` instead.
 */
export function activeLabel(
  record: ReturnRecordProjection | null,
): ReturnArtifactProjection | null {
  if (record === null) return null;
  return activeArtifacts(record, "SHIPPING_LABEL")[0] ?? null;
}

/** Packages that still count. A cancelled one satisfies nothing. */
export function activeShipments(
  record: ReturnRecordProjection,
): readonly ShipmentProjection[] {
  return (record.shipments ?? []).filter(
    (shipment) => shipment.shipmentStatus !== "CANCELLED",
  );
}

/** The RMAs on a case, or none. `null` and `[]` differ to a reader, not to a loop. */
export function caseRecords(
  projection: CaseProjection | null,
): readonly ReturnRecordProjection[] {
  return projection?.returnRecords ?? [];
}

/** Every non-cancelled package across every RMA on the case. */
export function caseShipments(
  projection: CaseProjection | null,
): readonly ShipmentProjection[] {
  return caseRecords(projection).flatMap((record) => activeShipments(record));
}

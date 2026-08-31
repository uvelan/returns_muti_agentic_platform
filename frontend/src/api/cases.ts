import { replaceEqualDeep } from "@tanstack/react-query";

import type { components } from "./generated/return-platform";
import { APIError, apiClient } from "./client";

/**
 * The case: one return, its RMAs, and which items each covers.
 *
 * Replaces the copilot's client-side join. S1 used to find a return by matching
 * `session.orderReference` against the top search candidate across two
 * unrelated collections -- so two open orders sharing a reference showed the
 * wrong one, and closing the tab lost the link entirely.
 *
 * Items arrive nested inside their record rather than as a flat list, which is
 * what makes "label LBL-1 belongs to RMA-2" unsayable by accident.
 */

/* -------------------------------------------------------------------------
 * The case projection -- the authoritative read contract (plan sect. 6.3).
 *
 * **Generated, not mirrored.** `GET /api/cases/{caseId}` is now declared over
 * `operations/case_projection/contract.py::CaseProjection`, so
 * `npm run contracts:generate` emits every type below and the console imports
 * them. The hand-rolled stand-ins that stood here while the route still served
 * `CaseDetail` are gone: a second spelling of the contract is a second thing to
 * keep in step, and the whole point of the OpenAPI gate is that there is one.
 *
 * What is *not* generated is everything under "Contract behaviour" further
 * down. Those are decisions about the contract -- when to ask again, which of
 * two answers to believe -- rather than its shape, and they outlive it.
 * ---------------------------------------------------------------------- */

/**
 * The generated shape as the backend actually serves it: `?: X | null` becomes
 * `: X | null`, all the way down.
 *
 * `openapi-typescript` renders a Pydantic `X | None = None` field as optional
 * *and* nullable, because JSON Schema marks it non-required. The backend never
 * omits it: `CaseProjection` is a response model, FastAPI serializes every
 * field, and `null` is what a block the platform has not computed is sent as --
 * which is the distinction the whole contract turns on.
 *
 * So `undefined` is a value this API cannot produce, and carrying it would make
 * every reader narrow a third case that never occurs. Worse, it would make
 * `value !== null` stop meaning "the platform said something", which is exactly
 * the check the panes are built out of.
 *
 * Applied at every level rather than only the top, so that a block read off the
 * projection and the same block named in a component's props are the same type.
 *
 * **Still load-bearing, and measured rather than assumed.** `CaseFactProjection`
 * no longer needs it -- its Python model dropped every default, so the published
 * document declares all eleven of its properties required and the generated type
 * arrives with no `?` at all. Every *other* alias below still does. Measured
 * against the published document, properties declared but not required:
 * `PolicyEvaluationProjection` 11, `WarehouseProjection` 11, `CaseProjection` 11,
 * `ReturnArtifactProjection` 8, `SupportProjection` 9, `PickupProjection` 8,
 * `ShipmentProjection` 7, `ReturnRecordProjection` 7, `ConfirmedOrderProjection` 7,
 * `ApprovedItemProjection` 5, `SelectedItemProjection` 5, `CustomerProjection` 4,
 * `SettlementProjection` 3, `CaseSummary` 2. Fourteen of fifteen consumers, so
 * this stays.
 *
 * It is kept over `CaseFactProjection` too, deliberately: removing it from that
 * one alias would buy nothing (the mapped type is now the identity there) while
 * making the fact projection the odd one out, and a reader would have to work
 * out why. The honest end state is that the *document* stops under-declaring for
 * the rest, at which point this alias is deleted outright rather than eroded
 * one type at a time -- a mapping type that is load-bearing for some of its
 * consumers and vestigial for others is the worst of both.
 *
 * Note this alias cannot be a verification instrument for that work: it strips
 * `?` regardless of what the document says, so an assertion written against it
 * passes whether the schema was fixed or not. The guard that actually holds the
 * required set is a data assertion over the generated document --
 * `frontend/scripts/check-served-fields.js`, run by `npm run contracts:check`,
 * which CI's `contract drift` job runs.
 */
type Served<T> = T extends readonly (infer Element)[]
  ? readonly Served<Element>[]
  : T extends object
    ? { [K in keyof T]-?: Served<Exclude<T[K], undefined>> }
    : T;

/** Where the workflow says the case is. Persisted, and the only authority. */
export type ReturnCaseStatus = components["schemas"]["ReturnCaseStatus"];

/** Which mode the Copilot renders. Derived on read by the backend, never stored. */
export type CopilotStage = components["schemas"]["CopilotStage"];

/**
 * What the platform is still waiting for. Two kinds of member: unresolved
 * dimensions say the completion profile could not be computed at all, and
 * requirement dimensions come out of the return-method requirement table.
 * Settlement is deliberately absent -- it never blocks completion.
 */
export type AwaitingDimension = components["schemas"]["AwaitingDimension"];

export type ShipmentStatus = components["schemas"]["ShipmentStatus"];

/** A bill of lading is not a shipping label; freight completion tells them apart. */
export type ReturnArtifactType = components["schemas"]["ReturnArtifactType"];

/** `NOT_INTEGRATED`, never `NOT_STARTED`: there is no producer, not an idle one. */
export type SettlementStatus = components["schemas"]["SettlementStatus"];

/**
 * One document belonging to one RMA, and to one of its packages once there is
 * one.
 *
 * **Artifacts hang off the record, never off the shipment**, and `shipmentId`
 * carries the whole of package attribution: `null` says this RMA has this
 * document and no package is known yet -- a label printed before anything was
 * tendered -- and a value says which package it goes on.
 */
export type ReturnArtifactProjection = Served<components["schemas"]["ReturnArtifactProjection"]>;

/** One package. Explicit fields only, and no `labelArtifacts`. */
export type ShipmentProjection = Served<components["schemas"]["ShipmentProjection"]>;

/** One RMA and everything belonging to it rather than to the case. */
export type ReturnRecordProjection = Served<components["schemas"]["ReturnRecordProjection"]>;

/** One line an RMA authorized, at the quantity it authorized. */
export type ApprovedItemProjection = Served<components["schemas"]["ApprovedItemProjection"]>;

/** One line the associate named that no RMA covers yet. */
export type SelectedItemProjection = Served<components["schemas"]["SelectedItemProjection"]>;

/** One fact at its latest value, with the provenance that decides trust. */
export type CaseFactProjection = Served<components["schemas"]["CaseFactProjection"]>;

export type CustomerProjection = Served<components["schemas"]["CustomerProjection"]>;
export type ConfirmedOrderProjection = Served<components["schemas"]["ConfirmedOrderProjection"]>;
export type PolicyEvaluationProjection = Served<components["schemas"]["PolicyEvaluationProjection"]>;
export type SupportProjection = Served<components["schemas"]["SupportProjection"]>;
export type PickupProjection = Served<components["schemas"]["PickupProjection"]>;
export type SettlementProjection = Served<components["schemas"]["SettlementProjection"]>;

/**
 * Bay placement, and receiving once there is any.
 *
 * Three of its fields have a producer today -- `facilityId`, `bayId` and
 * `bayReason`, all written by `ReturnCaseWorkflow`. The rest are declared and
 * always null, because the contract is where a producer will land and a field
 * the model cannot express is a field a writer cannot deliver.
 */
export type WarehouseProjection = Served<components["schemas"]["WarehouseProjection"]>;

/**
 * The wire contract: the state plus the four derived values.
 *
 * Every block is **nullable and absent rather than defaulted**. `artifacts: []`
 * means the platform looked and there are none; `artifacts: null` means it has
 * not computed them. Collapsing those two is how a pane renders "no label" for
 * a case whose label nobody has asked about yet.
 */
export type CaseProjection = Served<components["schemas"]["CaseProjection"]>;

/** Everything the platform has computed about a case, before deriving anything. */
export type CaseProjectionState = Omit<
  CaseProjection,
  "stage" | "awaiting" | "businessComplete" | "isTerminal"
>;

/**
 * The four derived values, and the revision they were derived at.
 *
 * Split out from `CaseProjection` because this is the whole of what Phase 8
 * reads: polling, staleness and reconnect are decided here and nowhere else.
 * A reader that needs a block reads the block; a reader that needs to know
 * whether to ask again reads this.
 */
export type CaseLifecycle = Pick<
  CaseProjection,
  "revision" | "updatedAt" | "stage" | "awaiting" | "businessComplete" | "isTerminal"
>;

/**
 * A row in the case list.
 *
 * Carries `stage` and `isTerminal` so a list can show the polling stop without
 * reading every case in full, and `status` in the **projected** vocabulary --
 * the same one the detail serves, so a row and the case it links to cannot
 * name one state two ways.
 */
export type CaseSummary = Served<components["schemas"]["CaseSummary"]>;

/* -------------------------------------------------------------------------
 * The bay recommendation.
 * ---------------------------------------------------------------------- */

/**
 * The bay recommendation, as `ReturnCaseWorkflow` records it on the case.
 *
 * **Kept, where `latestFacts` was deleted, and the asymmetry is the finding.**
 * `latestFacts` was a client-side duplicate of `latest_case_facts` and
 * `CaseProjection.facts` now serves exactly that projection, so the duplicate
 * went. This function is not a duplicate of anything: of the seven values it
 * reads, only three -- `bay_warehouse_reference`, `bay_reference` and
 * `bay_reason` -- have a typed home on `WarehouseProjection`.
 * `bay_return_location`, `bay_confidence_millionths`, `bay_evidence_reference`
 * and `bay_capacity_evidence` have **no field on the contract at all**, and
 * `WarehouseProjection.disposition` is documented as having no producer, so it
 * is not the home for the return location either. Deleting this would not
 * remove a client-side duplicate; it would delete four values the platform
 * publishes and nothing else exposes.
 *
 * It now reads `CaseProjection.facts`, which is already latest-per-name, so the
 * client-side reduction that used to happen here is gone -- the function is a
 * typed read of named facts and nothing more.
 *
 * **Best-effort by declared policy.** A case with no bay is the normal state of
 * a case whose workflow has not reached placement, or one where placement is
 * not configured -- `bay_reason` says which. It is not an error and must not be
 * rendered as one.
 *
 * Confidence is stored in millionths because the fact log holds no floats;
 * `confidence` below is the fraction, or null when nothing computed one. A
 * constant confidence would violate C2, so an absent one is reported absent
 * rather than defaulted to something that looks computed.
 */
export type BayRecommendation = {
  readonly warehouseReference: string | null;
  readonly bayReference: string | null;
  readonly returnLocation: string | null;
  readonly confidence: number | null;
  readonly reason: string | null;
  readonly evidenceReference: string | null;
  readonly capacityEvidence: string | null;
};

/**
 * One named fact off the projection.
 *
 * `null` for an absent fact and for one whose value is not a non-empty string,
 * which are the same thing to every caller: the platform did not say.
 */
function projectedFact(
  facts: readonly CaseFactProjection[] | null | undefined,
  name: string,
): CaseFactProjection | null {
  return facts?.find((fact) => fact.factName === name) ?? null;
}

function factString(
  facts: readonly CaseFactProjection[] | null | undefined,
  name: string,
): string | null {
  const value = projectedFact(facts, name)?.value;
  return typeof value === "string" && value !== "" ? value : null;
}

export function bayRecommendation(
  facts: readonly CaseFactProjection[] | null | undefined,
): BayRecommendation {
  const millionths = projectedFact(facts, "bay_confidence_millionths")?.value;
  return {
    warehouseReference: factString(facts, "bay_warehouse_reference"),
    bayReference: factString(facts, "bay_reference"),
    returnLocation: factString(facts, "bay_return_location"),
    confidence: typeof millionths === "number" ? millionths / 1_000_000 : null,
    reason: factString(facts, "bay_reason"),
    evidenceReference: factString(facts, "bay_evidence_reference"),
    capacityEvidence: factString(facts, "bay_capacity_evidence"),
  };
}

/** True when the workflow asked for a bay but nothing came back with a location. */
export function hasBayResult(bay: BayRecommendation): boolean {
  return bay.bayReference !== null || bay.returnLocation !== null;
}

/** One named fact's value as a string, for the panels that read a single one. */
export function projectedFactString(
  facts: readonly CaseFactProjection[] | null | undefined,
  name: string,
): string | null {
  return factString(facts, name);
}

/* -------------------------------------------------------------------------
 * Contract behaviour. Outlives the generated-type swap.
 * ---------------------------------------------------------------------- */

const COPILOT_STAGES: readonly string[] = [
  "DISCOVERY",
  "ORDER_CONFIRMATION",
  "ITEM_SELECTION",
  "RETURN_FACTS",
  "POLICY_EVALUATION",
  "APPROVAL_REQUIRED",
  "AWAITING_SUPPORT",
  "AUTHORIZED_RMA",
  "CARRIER_TRANSIT",
  "WAREHOUSE_RECEIVING",
  "RETURN_SETTLEMENT",
  "COMPLETED",
];

const AWAITING_DIMENSIONS: readonly string[] = [
  "POLICY",
  "RETURN_METHOD",
  "WARRANTY_VERIFICATION",
  "DELIVERY_CLAIM_VERIFICATION",
  "RECOVERY",
  "RMA",
  "LABEL",
  "TRACKING",
  "BOL",
  "PICKUP",
  "RETURN_LOCATION",
];

function isProjectionRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * The lifecycle envelope of a case read, or `null` when the payload carries none.
 *
 * **Structural, not a cast, and that is still the point.** The route serves
 * `CaseProjection` now, but these callers are handed `unknown` from a query
 * cache that may still be holding a body written before the swap, and a
 * `readProjection` whose response failed to decode is exactly the moment a cast
 * would assert a lifecycle that is not there. A body with no `revision` and no
 * `isTerminal` reads as `null`, and the callers below fall back to the safe
 * answer: keep asking, accept the response.
 *
 * Absence is never inferred as completion. A missing envelope means the client
 * does not know whether the case is finished, and "I do not know" must not stop
 * the poll -- that is the audit's defect in a different disguise.
 */
export function caseLifecycle(value: unknown): CaseLifecycle | null {
  if (!isProjectionRecord(value)) return null;

  const { revision, updatedAt, stage, awaiting, businessComplete, isTerminal } = value;

  if (typeof revision !== "number" || !Number.isInteger(revision) || revision < 0) return null;
  if (typeof updatedAt !== "string") return null;
  if (typeof stage !== "string" || !COPILOT_STAGES.includes(stage)) return null;
  if (typeof businessComplete !== "boolean" || typeof isTerminal !== "boolean") return null;
  if (!Array.isArray(awaiting)) return null;

  const dimensions: AwaitingDimension[] = [];
  for (const dimension of awaiting as readonly unknown[]) {
    if (typeof dimension !== "string" || !AWAITING_DIMENSIONS.includes(dimension)) return null;
    dimensions.push(dimension as AwaitingDimension);
  }

  return {
    revision,
    updatedAt,
    stage: stage as CopilotStage,
    awaiting: dimensions,
    businessComplete,
    isTerminal,
  };
}

/** How often an unfinished case is re-read (plan sect. 10.4). */
export const CASE_POLL_INTERVAL_MS = 10_000;

/**
 * Whether to ask again, and how soon.
 *
 * **`isTerminal`, and nothing else.**
 *
 * Not `returnRecords.length > 0`, which is what shipped: polling stopped the
 * moment an RMA existed, so a case with an RMA, null tracking and null label
 * froze on screen and the associate was shown a return that never appeared to
 * acquire a label. Plan sect. 14 forbids it by name -- "stop polling merely
 * because an RMA exists".
 *
 * Not `businessComplete` either. A rejected, cancelled or expired case is
 * never business-complete and is nonetheless finished; polling one forever
 * costs a request every ten seconds per open tab for a case that will never
 * change again.
 *
 * `RECOVERY_REQUIRED` is not in the terminal set, so a recovering case keeps
 * polling. That is intended: recovery restarts processing, and the client must
 * be there when it does.
 */
export function caseRefetchInterval(value: unknown, error?: unknown): number | false {
  if (isRefusal(error)) return false;
  return caseLifecycle(value)?.isTerminal === true ? false : CASE_POLL_INTERVAL_MS;
}

/**
 * A refusal the backend has already made up its mind about.
 *
 * Putting the case id in the URL makes it shareable, and a shareable id is one
 * that arrives stale, belonging to another tenant, or after the case was
 * archived. The answer is 403 or 404 and it will be the same answer in ten
 * seconds; polling it is a request every ten seconds, for as long as the tab
 * is open, to be told the same no.
 */
function isRefusal(error: unknown): boolean {
  return error instanceof APIError && error.status >= 400 && error.status < 500;
}

/**
 * How many times to re-ask after a failed read. Bounded, and not at all for a
 * refusal: an explained no does not become a yes on the second attempt.
 *
 * Three rather than one, because unlike most reads on this platform the case is
 * the screen -- an associate holding a box has nothing to look at without it --
 * and a transient blip during a handover is worth three attempts before the
 * ten-second poll takes over as the retry of last resort.
 */
export function caseRetry(failureCount: number, error: unknown): boolean {
  if (isRefusal(error)) return false;
  return failureCount < 3;
}

/**
 * Keep the newer of two case reads. Wired as React Query's `structuralSharing`,
 * so a rejected response never becomes query data at all.
 *
 * **Strictly lower is stale; equal never is.** Two polls can overlap and land
 * out of order -- the client holds revision 18 and a response carrying 17
 * arrives late -- and applying the late one walks the screen backwards past an
 * RMA the associate has already seen.
 *
 * Equal is deliberately *not* stale, and that is a live constraint rather than
 * a nicety. The revision invariant of plan sect. 6.5 is only partly in place:
 * until every child writer bumps the case revision, a projection can carry an
 * older revision than the children inside it. The assembler reads the case
 * document *first* precisely so the reported revision is never newer than the
 * data, which makes a same-revision response a legitimate carrier of new
 * children. Discarding equal revisions would drop exactly those updates -- the
 * label that arrived without a revision bump -- and reintroduce the frozen
 * screen from the other end.
 *
 * A read with no envelope on either side is accepted unchanged: a body that
 * carries no revision offers nothing to compare, and refusing it would leave
 * the screen empty rather than merely unordered.
 */
export function keepNewerRevision(previous: unknown, incoming: unknown): unknown {
  const held = caseLifecycle(previous);
  const arriving = caseLifecycle(incoming);
  if (held === null || arriving === null) return replaceEqualDeep(previous, incoming);
  if (arriving.revision < held.revision) return previous;
  return replaceEqualDeep(previous, incoming);
}

export const casesApi = {
  /**
   * The caller's own cases, newest first.
   *
   * `conversationId` narrows to the one that conversation raised. That is how
   * a resumed conversation gets its return back: the case id arrives on the
   * turn that confirmed, and a reopened conversation has no such turn.
   */
  async list(conversationId?: string): Promise<CaseSummary[]> {
    const query =
      conversationId === undefined ? "" : `?conversationId=${encodeURIComponent(conversationId)}`;
    const response = await apiClient<CaseSummary[]>(`/api/cases${query}`);
    return response.data ?? [];
  },

  /**
   * One case, as the projection. **This is the read.**
   *
   * `GET /api/cases/{caseId}` serves `CaseProjection`, the derived envelope
   * included, so a caller gets `stage`, `awaiting`, `businessComplete` and
   * `isTerminal` computed by the one function that is allowed to compute them.
   */
  async readProjection(caseId: string): Promise<CaseProjection> {
    const response = await apiClient<CaseProjection>(`/api/cases/${encodeURIComponent(caseId)}`);
    if (!response.data) throw new Error("The case could not be read.");
    return response.data;
  },
};

import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  CircleDashed,
  FileSearch,
  ShieldCheck,
  Tag,
} from "lucide-react";
import type { CaseProjection, ReturnRecordProjection } from "../../../api/cases";
import type { ExtractedField } from "../extractedFields";
import { COPILOT_TOKENS } from "../copilotTokens";
import { activeArtifacts, activeShipments, caseRecords, caseShipments } from "../types";

/**
 * What the platform has actually done, milestone by milestone.
 *
 * Every value here comes off `CaseProjection`. The pane used to read a
 * `ReturnSessionView` that is `null` for every Copilot return, which is why it
 * sat on "Ready" through an entire authorized RMA. **A value the platform has
 * not computed renders as `Pending`** rather than as a plausible one, and that
 * rule is older than this change -- `pairs` has always dropped nulls; the only
 * new part is that the values it is given are real.
 *
 * **A step lights on its own evidence, and on nothing before it.** Until now
 * `done` was `index <= furthest`, so the furthest step the platform had
 * evidenced back-filled every step under it as reached. That is a guess with a
 * good hit rate rather than a reading -- a case the workflow raised without a
 * confirmed order would have ticked "Order selected" on the strength of "Case
 * created" -- and a wrongly-lit step is worse than an unlit one. The badge
 * counts the steps that are lit, so the number and the ticks can no longer
 * disagree.
 *
 * **What is not evidenced, and stays dark.** A search that found orders is
 * recorded only as the turn's `query_evidence`, which the page holds as
 * `candidates` for the life of the mount. `GET /conversations/{id}/transcript`
 * replays role and text and nothing else, so after a reload -- or after
 * resuming a conversation that never confirmed -- there is no record of the
 * search to read, and "Orders identified" is drawn unreached. That is the
 * honest answer: the alternative is lighting it from the agent's prose, which
 * is the thing this pane exists to refuse. Every step from "Order selected"
 * onwards survives a reload, because each is evidenced by the case.
 */

type ProgressContext = {
  readonly candidates: readonly Record<string, unknown>[];
  readonly projection: CaseProjection | null;
};

type Milestone = {
  readonly label: string;
  readonly agent: string;
  readonly reached: (context: ProgressContext) => boolean;
  readonly output: (context: ProgressContext) => readonly (readonly [string, string])[];
};

const IDLE = new Set(["NOT_STARTED", "NOT_REQUIRED_OR_PENDING", "PENDING", "OPEN", ""]);

/** The word for a value the platform has not produced. Never a stand-in for one. */
const PENDING = "Pending";

function live(value: string | null | undefined): value is string {
  return typeof value === "string" && value.length > 0 && !IDLE.has(value);
}

function shown(value: string | null | undefined): string | null {
  return live(value) ? value : null;
}

function pairs(
  ...entries: readonly (readonly [string, string | null | undefined])[]
): readonly (readonly [string, string])[] {
  return entries.flatMap(([label, value]) =>
    value != null && value !== "" ? [[label, value] as const] : [],
  );
}

/** A projected enum as a sentence-shaped label. `NO_ELIGIBLE_BAY` reads badly. */
function humanized(value: string): string {
  const words = value.replace(/_/g, " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Whether the warehouse has recorded anything about these goods. Bay placement is not a receipt. */
function hasReceipt(projection: CaseProjection | null): boolean {
  const warehouse = projection?.warehouse ?? null;
  if (warehouse === null) return false;
  return (
    warehouse.receivedAt !== null ||
    warehouse.warehouseStatus !== null ||
    warehouse.receivedQuantity !== null ||
    warehouse.inspectionStatus !== null
  );
}

const MILESTONES: readonly Milestone[] = [
  {
    label: "Orders identified",
    agent: "Order Discovery",
    reached: ({ candidates, projection }) =>
      candidates.length > 0 || projection?.confirmedOrder != null,
    output: ({ candidates }) =>
      pairs(["Matches", candidates.length > 0 ? String(candidates.length) : null]),
  },
  {
    label: "Order selected",
    agent: "Order Discovery",
    reached: ({ projection }) => projection?.confirmedOrder != null,
    output: ({ projection }) =>
      pairs(
        ["Order", projection?.confirmedOrder?.orderReference],
        [
          // The name first. This read `customerReference ?? displayName`, so a
          // case that knew the customer was DUANE HOPKINS drew `600654` -- an
          // internal id, on the rail an associate reads while talking to that
          // customer. The agent is forbidden from showing a customer id in as
          // many words; the screen beside it should not either. The reference
          // stays as the fallback, because a case that has resolved an id and
          // not yet a name has something true to show.
          "Customer",
          projection?.customer?.displayName ?? projection?.customer?.customerReference,
        ],
      ),
  },
  {
    label: "Case created",
    agent: "Return Workflow",
    // **The projection is the case.** This used to read
    // `caseRecords(projection).length > 0 || projection?.support != null`, so
    // the step announcing that a case exists waited for an RMA or a Support
    // work item -- two stages further on. A case is created by the
    // confirmation itself (`confirm_case`, which also starts
    // `ReturnCaseWorkflow`), and `GET /api/cases/{caseId}` answers for a case
    // that exists and 404s for one that does not, so a projection in hand is
    // the record of the creation. Nothing weaker is being read here: the id
    // came off the confirming turn or the URL, and the platform served the
    // case under it.
    reached: ({ projection }) => projection !== null,
    output: ({ projection }) => pairs(["Support ticket", projection?.support?.workItemId]),
  },
  {
    label: "Shipment in progress",
    agent: "Return Fulfillment",
    reached: ({ projection }) =>
      caseShipments(projection).some((shipment) => live(shipment.shipmentStatus)),
    output: ({ projection }) => {
      const shipment = caseShipments(projection).find((candidate) =>
        live(candidate.shipmentStatus),
      );
      const method = caseRecords(projection).find((record) => live(record.returnMethod));
      return pairs(
        ["Status", shown(shipment?.shipmentStatus)],
        ["Method", method?.returnMethod],
      );
    },
  },
  {
    label: "Reached warehouse",
    agent: "Bay Allocation",
    reached: ({ projection }) => hasReceipt(projection),
    output: ({ projection }) => {
      const warehouse = projection?.warehouse ?? null;
      // A case with no bay is a normal state, and `bayReason` is the
      // explanation for it -- not an error, and not a blank where a bay
      // would be.
      const bay =
        warehouse?.bayId ??
        (warehouse?.bayReason == null ? null : humanized(warehouse.bayReason));
      return pairs(
        ["Status", shown(warehouse?.warehouseStatus)],
        ["Facility", warehouse?.facilityName ?? warehouse?.facilityId],
        ["Bay", bay],
      );
    },
  },
  {
    label: "Completed",
    agent: "Return Session",
    reached: ({ projection }) => projection?.businessComplete === true || projection?.isTerminal === true,
    output: ({ projection }) =>
      pairs(
        ["Resolution", shown(projection?.status)],
        ["Settlement", shown(projection?.settlement?.status)],
      ),
  },
];

/**
 * One RMA, its packages and its documents.
 *
 * Tracking renders `Pending` rather than disappearing: an RMA with a label and
 * no tracking is the shape of the real record `4e372a39…`, and a row that is
 * simply absent reads as a screen that has not loaded rather than as a platform
 * that has nothing to say yet.
 */
function ReturnRecordsPanel({ records }: { records: readonly ReturnRecordProjection[] }) {
  if (records.length === 0) return null;
  return (
    <div className="mt-3 flex flex-col gap-2">
      {records.map((record) => {
        const shipments = activeShipments(record);
        const labels = activeArtifacts(record, "SHIPPING_LABEL");
        const bols = activeArtifacts(record, "BILL_OF_LADING");
        // Attribution is `shipmentId` and nothing else. A label with no
        // shipment belongs to the RMA and to no package yet.
        const unattributedLabels = labels.filter((artifact) => artifact.shipmentId === null);
        const rows: readonly (readonly [string, string])[] = [
          ...(shipments.length === 0
            ? ([["Tracking", PENDING]] as const)
            : shipments.map(
                (shipment) =>
                  [
                    `Tracking · ${shipment.shipmentId}`,
                    shipment.trackingNumber ?? PENDING,
                  ] as const,
              )),
          ...shipments.flatMap((shipment) =>
            labels
              .filter((artifact) => artifact.shipmentId === shipment.shipmentId)
              .map(
                (artifact) =>
                  [
                    `Label · ${shipment.shipmentId}`,
                    artifact.fileName ?? artifact.artifactId,
                  ] as const,
              ),
          ),
          ...(unattributedLabels.length > 0
            ? unattributedLabels.map(
                (artifact) => ["Label", artifact.fileName ?? artifact.artifactId] as const,
              )
            : labels.length === 0
              ? ([["Label", PENDING]] as const)
              : []),
          ...bols.map(
            (artifact) => ["BOL", artifact.fileName ?? artifact.artifactId] as const,
          ),
          ["Return to", record.returnLocation ?? PENDING] as const,
        ];
        const covers = (record.approvedItems ?? []).flatMap((item) =>
          item.orderLineReference === null ? [] : [item.orderLineReference],
        );

        return (
          <div
            key={record.returnRecordId}
            className="rounded-lg border border-outline-variant/40 bg-surface-container-low p-2.5 shadow-sm"
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="truncate text-xs font-semibold text-on-surface">
                {record.returnReference ?? "RMA pending"}
              </span>
              <span className="shrink-0 text-xs font-medium text-primary">
                {record.status ?? PENDING}
              </span>
            </div>
            <dl className="mt-1 flex flex-col gap-0.5">
              {rows.map(([label, value]) => (
                <div key={label} className="flex gap-2 text-xs">
                  <dt className="shrink-0 text-outline">{label}</dt>
                  <dd className="min-w-0 truncate text-on-surface" title={value}>
                    {value}
                  </dd>
                </div>
              ))}
            </dl>
            {covers.length > 0 ? (
              <p className="mt-1 truncate text-xs text-outline">Covers {covers.join(", ")}</p>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

export type ProgressTruthPaneProps = {
  candidates: readonly Record<string, unknown>[];
  /**
   * The return-setup fields the conversation and the case have captured.
   *
   * **A field list, not a transcript.** This prop replaced a
   * `ResponseStatement[]`, and the reason is the whole point of the panel: a
   * statement is prose the agent uttered -- including, in a live run, "Line 1
   * has no product, quantity or amount recorded against it" -- and rendering
   * narration of the agent's own reasoning under a heading promising extracted
   * facts exposes internal working as though it were established fact.
   * `extractedReturnFields` decides what is here, and it is bounded by design.
   */
  fields: readonly ExtractedField[];
  projection: CaseProjection | null;
  caseId?: string | null;
};

export function ProgressTruthPane({
  candidates,
  fields,
  projection,
  caseId,
}: ProgressTruthPaneProps) {
  const context: ProgressContext = { candidates, projection };
  const records = caseRecords(projection);
  // Asked once per milestone, so the ticks, the connectors and the count in the
  // header are three readings of the same answer rather than three predicates
  // that can disagree -- which is how the header came to say "1/6" beside a
  // step nobody had ticked.
  const reached = MILESTONES.map((milestone) => milestone.reached(context));
  const reachedCount = reached.filter(Boolean).length;
  // The last step with evidence. Only the spinner uses it: the step after the
  // furthest evidenced one is the one being worked on.
  const furthest = reached.reduce<number>((best, done, index) => (done ? index : best), -1);

  return (
    <section className={COPILOT_TOKENS.layout.pane}>
      {/* 1. TOP DIV: Workflow Progress Stepper (Independent scroll, invisible scrollbar) */}
      <div className="flex flex-1 min-h-0 flex-col border-b border-outline-variant/25 overflow-hidden">
        {/* Header for Progress */}
        <header className="flex h-[52px] shrink-0 items-center justify-between border-b border-outline-variant/20 px-4 bg-surface-container-lowest/80 backdrop-blur-sm">
          <h2 className="text-sm font-semibold text-on-surface">Progress</h2>
          <span className="rounded-full bg-secondary-container px-2.5 py-0.5 text-xs font-medium text-primary">
            {reachedCount > 0
              ? `${String(reachedCount)}/${String(MILESTONES.length)} milestones`
              : "Ready"}
          </span>
        </header>

        {/* Stepper Scroll Container */}
        <div className="flex-1 overflow-y-auto p-4 hide-scrollbar">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-outline mb-3">
            Workflow Progress
          </h3>

          <ol className="flex flex-col">
            {MILESTONES.map((milestone, index) => {
              const done = reached[index];
              // The step after the furthest evidenced one, which by
              // construction is never itself evidenced. Only meaningful once a
              // case exists -- before that nobody is working the return.
              const active = index === furthest + 1 && projection !== null;
              const output = milestone.output(context);
              // The tick, the spinner and the empty circle are the whole of
              // what this pane says about a step, and to a screen reader they
              // were three undifferentiated decorative glyphs. The state is
              // named here so it is readable rather than merely visible.
              const state = done ? "reached" : active ? "in progress" : "not reached";

              return (
                <li key={milestone.label} className="relative flex gap-3 pb-3.5 last:pb-0">
                  {/* Step Connector Line */}
                  {index < MILESTONES.length - 1 ? (
                    <span
                      aria-hidden="true"
                      className={[
                        "absolute left-[9px] top-5 w-px h-[calc(100%-0.875rem)]",
                        done ? "bg-primary" : "bg-outline-variant/40",
                      ].join(" ")}
                    />
                  ) : null}

                  {/* Step Icon */}
                  <span
                    role="img"
                    aria-label={`${milestone.label}: ${state}`}
                    className="relative z-10 flex size-5 shrink-0 items-center justify-center mt-0.5"
                  >
                    {done ? (
                      <CheckCircle2 size={18} className="fill-primary text-on-primary" />
                    ) : active ? (
                      <CircleDashed size={16} className="text-primary animate-spin" />
                    ) : (
                      <Circle size={15} className="text-outline-variant" />
                    )}
                  </span>

                  {/* Step Body */}
                  <div className="min-w-0 flex-1">
                    <span
                      className={`block text-xs font-semibold leading-tight ${
                        done
                          ? "text-on-surface"
                          : active
                            ? "text-primary font-bold"
                            : "text-outline"
                      }`}
                    >
                      {milestone.label}
                    </span>
                    <span className="block text-[11px] text-outline mt-0.5">
                      {milestone.agent}
                    </span>

                    {output.length > 0 && !(milestone.label === "Case created" && records.length > 0) ? (
                      <dl className="mt-1.5 flex flex-col gap-0.5 rounded bg-surface-container-low p-2">
                        {output.map(([label, value]) => (
                          <div key={label} className="flex justify-between gap-2 text-xs">
                            <dt className="shrink-0 text-outline">{label}</dt>
                            <dd className="min-w-0 truncate font-medium text-on-surface" title={value}>
                              {value}
                            </dd>
                          </div>
                        ))}
                      </dl>
                    ) : null}

                    {milestone.label === "Case created" ? (
                      <ReturnRecordsPanel records={records} />
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ol>
        </div>
      </div>

      {/* 2. BOTTOM DIV: Extracted & Verified Facts (Independent scroll, invisible scrollbar) */}
      <div className="flex flex-1 min-h-0 flex-col overflow-hidden bg-surface-container-lowest">
        {/* Header for Facts */}
        <header className="flex h-[44px] shrink-0 items-center justify-between border-b border-outline-variant/20 px-4 bg-surface-container-low/50">
          <div className="flex items-center gap-1.5">
            <Tag size={13} className="text-outline" />
            <h3 className="text-xs font-semibold uppercase tracking-wider text-outline">
              Extracted &amp; Verified Facts
            </h3>
          </div>
          {caseId ? (
            <span className="font-mono text-[11px] font-semibold text-primary truncate max-w-[120px]" title={caseId}>
              {caseId}
            </span>
          ) : null}
        </header>

        {/* Facts Scroll Container */}
        <div className="flex-1 overflow-y-auto p-4 hide-scrollbar">
          {fields.length > 0 ? (
            <dl className="flex flex-col gap-1.5">
              {fields.map((field) => (
                <div
                  key={field.key}
                  className="flex items-baseline justify-between gap-2 rounded-lg border border-outline-variant/40 bg-surface-container px-2.5 py-1.5 shadow-xs"
                >
                  <dt className="flex shrink-0 items-center gap-1.5 text-xs text-outline">
                    {/* The icon is the only thing separating what the platform
                        recorded from what somebody said, at a glance, so it is
                        not decoration. */}
                    {field.provenance === "RECORDED" ? (
                      <ShieldCheck
                        size={12}
                        className="text-primary"
                        aria-label="Recorded by the platform"
                      />
                    ) : (
                      <Tag size={12} className="text-outline" aria-label="Stated by the associate" />
                    )}
                    <span>{field.label}</span>
                  </dt>
                  <dd
                    className="min-w-0 truncate text-right text-xs font-medium text-on-surface"
                    title={field.value}
                  >
                    {field.value}
                    {field.unsettledBecause === null ? null : (
                      <span className="ml-1.5 inline-flex items-center gap-1 text-[10px] font-semibold text-amber-700">
                        <AlertTriangle size={10} />
                        {field.unsettledBecause}
                      </span>
                    )}
                  </dd>
                </div>
              ))}
            </dl>
          ) : (
            <div className="flex h-full min-h-[120px] flex-col items-center justify-center rounded-xl border border-dashed border-outline-variant/50 bg-surface-container-low/30 p-4 text-center">
              <div className="flex size-9 items-center justify-center rounded-full bg-surface-container mb-2 text-outline">
                <FileSearch size={18} />
              </div>
              <h4 className="text-xs font-semibold text-on-surface-variant mb-1">
                No return details captured yet
              </h4>
              <p className="text-xs text-outline max-w-[200px] leading-relaxed">
                SKU, quantity, colour or finish, reason and order number appear here as the
                conversation establishes them.
              </p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

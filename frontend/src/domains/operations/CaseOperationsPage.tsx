import { useMemo, useState } from "react";
import { skipToken, useQuery } from "@tanstack/react-query";

import {
  bayRecommendation,
  casesApi,
  hasBayResult,
  projectedFactString,
  type CaseFactProjection,
  type CaseProjection,
  type CaseSummary,
} from "../../api/cases";
import {
  adoptionGap,
  configApi,
  type ProcessClassAdoption,
  type ReleaseAdoptionState,
} from "../../api/configuration";
import { supportApi, type SupportMessage } from "../../api/support";
import { useCapabilities } from "../../hooks/capabilityContext";
import { DomainRail, RailFact, RailNote, RailSection } from "../DomainRail";

/**
 * UI-04 -- one case, operationally.
 *
 * The question this answers is "what is this case waiting on, and what may I do
 * about it". Everything on it is read from a backend field; where the platform
 * publishes no field, this screen says so in those words rather than showing a
 * plausible placeholder. A fabricated "HEALTHY" is worse than an admitted gap,
 * because the gap is fixable and the fabrication is trusted.
 *
 * Three things an operator asks that no API answers today, and which are
 * therefore stated as unavailable rather than invented:
 *
 *   - **Live workflow execution status.** `ReturnCaseWorkflow` has an
 *     `execution_state` query carrying status, reminders sent, and whether bay
 *     and support resolved -- and no HTTP route calls it.
 *   - **The business-calendar deadline.** `ReturnCaseTimings` is workflow input.
 *     The Channel B work item's `slaDueAt` is a real deadline and is shown as
 *     what it is -- the support SLA -- not relabelled as the workflow's.
 *   - **A failure or blocker code.** No case field and no fact carries one.
 *
 * **This screen reads `CaseProjection`.** That changed what it can say, in both
 * directions, and the change is stated on each panel rather than papered over.
 *
 * *Gained:* `stage`, `awaiting`, `businessComplete` and `isTerminal` -- computed
 * by the backend from the release's return-method requirement table. "What is
 * this case waiting on" is the question this screen exists to answer, and it is
 * now answered by a computation instead of inferred from which fields happen to
 * be null.
 *
 * *Lost:* the persistence and sync fields (`workflowId`, `sessionId`,
 * `configurationReleaseId`, `graphGenerationId`) and the **append-only** fact
 * log. `CaseProjection` is the business read: it carries facts as the
 * latest-per-name projection with provenance, and it carries nothing about how
 * the case was stored or which execution owns it. Both absences are named where
 * they bite, because a panel that quietly showed less would be worse than one
 * that says what it can no longer see.
 *
 * `/api/config/audit` is still deliberately not queried here: it is
 * platform-scoped -- promotions and source edits -- so it would answer a
 * different question while looking like this one's.
 */

//: The **projected** vocabulary, which is what `CaseSummary.status` now
//: carries. `COMPLETED` used to be listed here and matched nothing: the
//: persisted status for a finished case is `CLOSED`, so the filter was dead the
//: whole time. It works now, and `COMPLETED_EXTERNAL_SETTLEMENT` is beside it
//: because that is where every closed case actually lands while no settlement
//: producer exists.
const STATUS_FILTERS = [
  "All",
  "GATHERING_INFO",
  "AWAITING_SUPPORT",
  "PROCESSING_RETURN",
  "COMPLETED_EXTERNAL_SETTLEMENT",
] as const;

export function CaseOperationsPage() {
  const { can } = useCapabilities();
  const [status, setStatus] = useState<string>("All");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const canRead = can("returns.session.read");

  const cases = useQuery({
    queryKey: ["cases", "list"],
    queryFn: () => casesApi.list(),
    enabled: canRead,
  });

  const detail = useQuery({
    queryKey: ["cases", selectedId],
    queryFn: selectedId === null ? skipToken : () => casesApi.readProjection(selectedId),
  });

  const adoption = useQuery({
    queryKey: ["config", "adoption"],
    queryFn: () => configApi.adoption(),
    enabled: can("config.runtime.read"),
  });

  const visible = useMemo(
    () => (cases.data ?? []).filter((entry) => status === "All" || entry.status === status),
    [cases.data, status],
  );

  if (!canRead) {
    return (
      <p className="text-sm text-on-surface-variant">
        You do not have access to cases. Reading them requires returns.session.read.
      </p>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,4fr)_minmax(0,9fr)]">
      {/*
        Rail context from what this screen already holds. The adoption answer
        belongs here rather than only in the case panel: "is the platform
        running the release it says it activated" is true of every case on the
        list, so repeating it per case would be repeating it.
      */}
      <DomainRail>
        <RailSection title="Cases">
          <RailFact label="Total" value={cases.data?.length ?? null} />
          <RailFact label="Showing" value={cases.isPending ? null : visible.length} />
          <RailFact label="Selected" value={selectedId} />
        </RailSection>
        <RailSection title="Release adoption">
          {adoption.error !== null ? (
            <RailNote>Adoption reporting is unavailable.</RailNote>
          ) : adoption.data === undefined ? (
            <RailNote>
              {can("config.runtime.read")
                ? "Loading..."
                : "Requires config.runtime.read, which you do not hold."}
            </RailNote>
          ) : (
            <>
              <RailFact label="Status" value={adoption.data.status} />
              <RailFact label="Revision" value={adoption.data.activated_head_revision} />
              {adoption.data.pending_process_classes.length === 0 ? (
                <RailNote>Every required process class is on the activated revision.</RailNote>
              ) : (
                <RailNote>
                  Waiting on {adoption.data.pending_process_classes.join(", ")}.
                </RailNote>
              )}
            </>
          )}
        </RailSection>
      </DomainRail>
      <CaseListPane
        cases={visible}
        total={cases.data?.length ?? 0}
        loading={cases.isPending}
        error={cases.error}
        status={status}
        onStatusChange={setStatus}
        selectedId={selectedId}
        onSelect={setSelectedId}
      />
      <CaseDetailPane
        detail={detail.data ?? null}
        loading={selectedId !== null && detail.isPending}
        error={detail.error}
        adoption={adoption.data ?? null}
        adoptionError={adoption.error}
        adoptionLoading={adoption.isPending && can("config.runtime.read")}
      />
    </div>
  );
}

function Panel({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-2 rounded-md border border-outline-variant p-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-outline">{title}</h3>
      {note === undefined ? null : <p className="text-[11px] text-on-surface-variant">{note}</p>}
      {children}
    </section>
  );
}

function Field({ label, value }: { label: string; value: string | number | null }) {
  if (value === null || value === "") return null;
  return (
    <div className="flex min-w-0 gap-1.5">
      <dt className="shrink-0 text-outline">{label}</dt>
      <dd className="min-w-0 break-all text-on-surface">{String(value)}</dd>
    </div>
  );
}

/** An answer the platform does not publish, said as that rather than left blank. */
function NotPublished({ what, because }: { what: string; because: string }) {
  return (
    <p className="text-[11px] text-on-surface-variant">
      <span className="text-outline">{what}:</span> not published by any API. {because}
    </p>
  );
}

function CaseListPane({
  cases,
  total,
  loading,
  error,
  status,
  onStatusChange,
  selectedId,
  onSelect,
}: {
  cases: readonly CaseSummary[];
  total: number;
  loading: boolean;
  error: Error | null;
  status: string;
  onStatusChange: (value: string) => void;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <section className="flex max-h-[calc(100vh-8rem)] min-h-0 flex-col overflow-hidden rounded-lg border border-outline-variant bg-surface-container-lowest">
      <h2 className="border-b border-outline-variant px-4 py-3 text-sm font-semibold text-on-surface">
        Cases
      </h2>
      <div className="flex flex-wrap gap-1.5 border-b border-outline-variant px-3 py-2">
        {STATUS_FILTERS.map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => { onStatusChange(option); }}
            className={`rounded-full border px-3 py-1 text-xs transition ${
              status === option
                ? "border-primary text-primary"
                : "border-outline-variant text-on-surface-variant hover:border-primary hover:text-primary"
            }`}
          >
            {option === "All" ? `All (${String(total)})` : option}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto">
        {/*
          Three states. `data ?? []` would report "no cases" for a request that
          failed, and an operator acts on an empty case list by walking away.
        */}
        {error !== null ? (
          <p role="alert" className="px-4 py-3 text-sm text-error">
            {error.message}
          </p>
        ) : loading ? (
          <p className="px-4 py-3 text-sm text-on-surface-variant">Loading...</p>
        ) : cases.length === 0 ? (
          <p className="px-4 py-3 text-sm text-on-surface-variant">
            {total === 0 ? "No cases yet." : "No case has that status."}
          </p>
        ) : (
          <ul>
            {cases.map((entry) => (
              <li key={entry.caseId}>
                <button
                  type="button"
                  onClick={() => { onSelect(entry.caseId); }}
                  className={`flex w-full flex-col gap-0.5 border-b border-outline-variant px-4 py-2.5 text-left transition hover:bg-surface-container ${
                    entry.caseId === selectedId ? "bg-surface-container" : ""
                  }`}
                >
                  <span className="min-w-0 break-all text-sm text-on-surface">
                    {entry.confirmedOrderReference ?? entry.caseId}
                  </span>
                  <span className="flex flex-wrap items-center gap-2 text-[11px] text-outline">
                    <span>{entry.status}</span>
                    <span>
                      {entry.returnRecordCount} RMA{entry.returnRecordCount === 1 ? "" : "s"}
                    </span>
                    <span>{entry.updatedAt}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function CaseDetailPane({
  detail,
  loading,
  error,
  adoption,
  adoptionError,
  adoptionLoading,
}: {
  detail: CaseProjection | null;
  loading: boolean;
  error: Error | null;
  adoption: ReleaseAdoptionState | null;
  adoptionError: Error | null;
  adoptionLoading: boolean;
}) {
  if (error !== null) {
    return (
      <section className="rounded-lg border border-outline-variant bg-surface-container-lowest p-4">
        <p role="alert" className="text-sm text-error">
          {error.message}
        </p>
      </section>
    );
  }
  if (loading) {
    return (
      <section className="rounded-lg border border-outline-variant bg-surface-container-lowest p-4">
        <p className="text-sm text-on-surface-variant">Loading...</p>
      </section>
    );
  }
  if (detail === null) {
    return (
      <section className="rounded-lg border border-outline-variant bg-surface-container-lowest p-6">
        <p className="text-sm text-on-surface-variant">
          Pick a case to see what it is waiting on, and what you may do about it.
        </p>
      </section>
    );
  }

  return (
    <div className="flex max-h-[calc(100vh-8rem)] flex-col gap-3 overflow-y-auto rounded-lg border border-outline-variant bg-surface-container-lowest p-4">
      <StatePanel detail={detail} />
      <LifecyclePanel detail={detail} />
      <ReleasePanel adoption={adoption} error={adoptionError} loading={adoptionLoading} />
      <GraphPanel detail={detail} />
      <RmaPanel detail={detail} />
      <ChannelBPanel detail={detail} />
      <InterventionPanel detail={detail} />
      <AuditPanel facts={detail.facts} />
    </div>
  );
}

function StatePanel({ detail }: { detail: CaseProjection }) {
  const bay = bayRecommendation(detail.facts);
  return (
    <Panel title="Case">
      <dl className="flex flex-col gap-1 text-[11px]">
        <Field label="Status" value={detail.status} />
        <Field label="Order" value={detail.confirmedOrder?.orderReference ?? null} />
        <Field label="Case" value={detail.caseId} />
        <Field label="Tenant" value={detail.tenantId} />
        <Field label="Owner" value={detail.principalId} />
        <Field label="Branch" value={detail.customer?.branchReference ?? null} />
        <Field label="Customer" value={detail.customer?.customerReference ?? null} />
        <Field label="Revision" value={detail.revision} />
        <Field label="Updated" value={detail.updatedAt} />
        <Field label="Confirmation key" value={detail.confirmedOrder?.confirmationKey ?? null} />
        <Field
          label="Bay"
          value={
            hasBayResult(bay)
              ? [bay.warehouseReference, bay.bayReference, bay.returnLocation]
                  .filter((part) => part !== null)
                  .join(" / ")
              : null
          }
        />
      </dl>
      {hasBayResult(bay) ? null : (
        <p className="text-[11px] text-on-surface-variant">
          No bay recommended. Placement is best-effort, so this is a state rather than a fault
          {bay.reason === null ? "" : ` (${bay.reason})`}.
        </p>
      )}
    </Panel>
  );
}

/**
 * Where the case is, and what it is waiting for. Replaces the execution panel.
 *
 * The execution panel read `workflowId` and `sessionId` off `CaseView` and used
 * a null `workflowId` as a proxy for "nothing is going to happen to this case".
 * `CaseProjection` carries neither field -- it is the business read, and the
 * durable execution is how the platform runs the case rather than what the case
 * is -- so the proxy is gone.
 *
 * What replaces it is better than what it approximated. `awaiting` is computed
 * from the release's return-method requirement table, `stage` from the frozen
 * precedence, and `isTerminal` from the persisted status alone. A case waiting
 * on nothing while neither complete nor terminal is exactly the stuck state the
 * missing-workflow alert was reaching for, and it is now a statement rather than
 * an inference.
 */
function LifecyclePanel({ detail }: { detail: CaseProjection }) {
  const stalled = detail.awaiting.length === 0 && !detail.businessComplete && !detail.isTerminal;
  return (
    <Panel title="Lifecycle">
      <dl className="flex flex-col gap-1 text-[11px]">
        <Field label="Stage" value={detail.stage} />
        <Field label="Waiting on" value={detail.awaiting.join(", ")} />
        <Field label="Business complete" value={detail.businessComplete ? "yes" : "no"} />
        <Field label="Terminal" value={detail.isTerminal ? "yes" : "no"} />
      </dl>
      {stalled ? (
        <p role="alert" className="text-[11px] text-error">
          This case is waiting on nothing, is not complete, and is not finished. Nothing will move
          it without an intervention below.
        </p>
      ) : null}
      <NotPublished
        what="Durable execution"
        because={
          "CaseProjection carries no workflowId or sessionId -- it is the business read, not the "
          + "persistence record. The lifecycle above is what the case is waiting for; it does not "
          + "prove a Temporal execution is alive."
        }
      />
      <NotPublished
        what="Live execution state"
        because={
          "ReturnCaseWorkflow answers an execution_state query carrying status, reminders sent "
          + "and whether bay and support resolved, and no HTTP route calls it."
        }
      />
      <NotPublished
        what="Failure or blocker"
        because="No case field and no case fact carries one."
      />
    </Panel>
  );
}

/**
 * Whether the release the platform says it activated is the one running.
 *
 * It used to also show which release *this case* was created under, read from
 * `CaseView.configurationReleaseId`, and compare the two -- the disagreement
 * during an `ACTIVATING` window being the reason contract C5 exists.
 * `CaseProjection` does not carry that field, so the comparison is gone and the
 * panel is stated as answering the platform-wide half only. Inventing the
 * missing half from the activated release would make every case look adopted.
 */
function ReleasePanel({
  adoption,
  error,
  loading,
}: {
  adoption: ReleaseAdoptionState | null;
  error: Error | null;
  loading: boolean;
}) {
  return (
    <Panel title="Configuration release and adoption">
      <NotPublished
        what="The release this case ran under"
        because={
          "CaseProjection carries no configurationReleaseId, so this case cannot be compared "
          + "against the activated release. Adoption below is platform-wide."
        }
      />
      {error !== null ? (
        // Reporting unavailable, not "everything adopted". The route 503s when
        // the adoption store is absent, and an empty state rendered as LIVE
        // would be the exact lie C5 exists to prevent.
        <p role="alert" className="text-[11px] text-error">
          Adoption reporting is unavailable: {error.message}
        </p>
      ) : loading ? (
        <p className="text-[11px] text-on-surface-variant">Loading...</p>
      ) : adoption === null ? (
        <p className="text-[11px] text-on-surface-variant">
          Adoption requires config.runtime.read, which you do not hold.
        </p>
      ) : (
        <AdoptionDetail adoption={adoption} />
      )}
    </Panel>
  );
}

export function AdoptionDetail({
  adoption,
  caseRelease,
}: {
  adoption: ReleaseAdoptionState;
  caseRelease?: string | null;
}) {
  const behind =
    caseRelease != null
    && adoption.activated_release_id !== null
    && caseRelease !== adoption.activated_release_id;
  return (
    <div className="flex flex-col gap-1.5 text-[11px]">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full bg-surface-container px-2 py-0.5 text-on-surface">
          {adoption.status}
        </span>
        {adoption.status === "ACTIVATING" ? (
          <span className="text-on-surface-variant">
            The platform is running two releases at once.
          </span>
        ) : null}
        {adoption.status === "NO_ACTIVE_RELEASE" ? (
          <span className="text-on-surface-variant">No release has been activated.</span>
        ) : null}
      </div>
      <dl className="flex flex-col gap-1">
        <Field label="Activated release" value={adoption.activated_release_id} />
        <Field label="Head revision" value={adoption.activated_head_revision} />
        <Field label="Evaluated" value={adoption.evaluated_at} />
      </dl>
      {behind ? (
        <p className="text-on-surface-variant">
          This case ran under a different release from the activated one.
        </p>
      ) : null}
      {adoption.pending_process_classes.length === 0 ? null : (
        <div className="flex flex-col gap-0.5">
          {/*
            Named, not counted. "3 of 5 pending" does not tell an operator which
            container to go and look at, which is the only thing they can act on.
          */}
          <span className="text-outline">Waiting on</span>
          <ul className="flex flex-wrap gap-1.5">
            {adoption.pending_process_classes.map((name) => (
              <li key={name} className="rounded bg-surface-container px-1.5 py-0.5 font-mono">
                {name}
              </li>
            ))}
          </ul>
        </div>
      )}
      <ul className="flex flex-col gap-0.5">
        {adoption.process_classes.map((entry) => (
          <ProcessClassRow key={entry.process_class} adoption={entry} />
        ))}
      </ul>
    </div>
  );
}

/**
 * One process class, with the two non-adoption reasons kept apart.
 *
 * `live_instances === 0` means nothing is deployed and the fix is to start it.
 * `live > adopted` means it is deployed and behind, and the fix is to wait for
 * its poll or find out why it is stuck. A single "not adopted" badge would send
 * an operator down the wrong one about half the time.
 */
function ProcessClassRow({ adoption }: { adoption: ProcessClassAdoption }) {
  const gap = adoptionGap(adoption);
  const says = {
    ADOPTED: "adopted",
    NOT_DEPLOYED: "not deployed",
    BEHIND: "deployed, behind",
    PARTIAL: "partly adopted",
  }[gap];
  return (
    <li className="flex min-w-0 flex-wrap items-baseline gap-x-2">
      <span className="min-w-0 break-all font-mono text-on-surface">{adoption.process_class}</span>
      <span className={gap === "ADOPTED" ? "text-on-surface-variant" : "text-outline"}>{says}</span>
      <span className="text-outline">
        {adoption.adopted_instances}/{adoption.live_instances} live
      </span>
      {adoption.required ? null : <span className="text-outline">(optional)</span>}
    </li>
  );
}

function GraphPanel({ detail }: { detail: CaseProjection }) {
  const projectedId = projectedFactString(detail.facts, "return_graph_generation_id");
  return (
    <Panel title="Graph and sync">
      <dl className="flex flex-col gap-1 text-[11px]">
        <Field label="Projected into" value={projectedId} />
      </dl>
      {projectedId === null ? (
        <p className="text-[11px] text-on-surface-variant">
          Nothing about this case has been projected into a graph generation yet.
        </p>
      ) : null}
      <NotPublished
        what="The generation this case was created against"
        because={
          "CaseProjection carries no graphGenerationId, so a case created against one generation "
          + "and projected into another can no longer be told apart here."
        }
      />
    </Panel>
  );
}

function RmaPanel({ detail }: { detail: CaseProjection }) {
  const records = detail.returnRecords ?? [];
  const unassigned = detail.selectedItems ?? [];
  const fulfillment = projectedFactString(detail.facts, "fulfillment_status");
  const evidence = projectedFactString(detail.facts, "shipment_evidence");
  return (
    <Panel title={`RMAs (${String(records.length)})`}>
      {records.length === 0 ? (
        <p className="text-[11px] text-on-surface-variant">No RMA has been issued yet.</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {records.map((record) => {
            const lines = record.approvedItems ?? [];
            const shipments = record.shipments ?? [];
            // The live label, never `artifacts[0]`: a replaced one stays on the
            // record for audit, and the first is whichever Mongo returned.
            const label =
              record.artifacts?.find(
                (artifact) =>
                  artifact.artifactType === "SHIPPING_LABEL"
                  && artifact.active === true
                  && artifact.supersededBy === null,
              ) ?? null;
            return (
              <li
                key={record.returnRecordId}
                className="flex min-w-0 flex-col gap-0.5 rounded bg-surface-container-low px-2 py-1.5 text-[11px]"
              >
                <span className="flex flex-wrap items-baseline gap-2">
                  <span className="min-w-0 break-all font-mono text-on-surface">
                    {record.returnReference ?? "not yet numbered"}
                  </span>
                  <span className="text-outline">{record.status}</span>
                  <span className="text-outline">
                    {lines.length} line{lines.length === 1 ? "" : "s"}
                  </span>
                </span>
                {/* RMA-scoped, and shown inside the RMA for that reason. */}
                <dl className="flex flex-col gap-0.5">
                  <Field label="Label" value={label?.artifactId ?? null} />
                  <Field
                    label="Tracking"
                    value={
                      shipments
                        .map((shipment) => shipment.trackingNumber)
                        .filter((tracking) => tracking !== null)
                        .join(", ") || null
                    }
                  />
                  <Field label="Method" value={record.returnMethod} />
                  <Field label="Return to" value={record.returnLocation} />
                </dl>
                {label !== null && shipments.length === 0 ? (
                  // The real stuck shape. Said out loud rather than left as two
                  // fields where one is filled and one is blank.
                  <p className="text-on-surface-variant">
                    A label exists and no package has been tendered, so there is no tracking
                    number to show.
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      {unassigned.length > 0 ? (
        <p className="text-[11px] text-on-surface-variant">
          {unassigned.length} line{unassigned.length === 1 ? "" : "s"} that no RMA covers yet.
        </p>
      ) : null}
      <dl className="flex flex-col gap-1 text-[11px]">
        <Field label="Fulfilment" value={fulfillment} />
        <Field label="Shipment evidence" value={evidence} />
      </dl>
      {fulfillment === null ? (
        <p className="text-[11px] text-on-surface-variant">
          No shipment has been observed against this case.
        </p>
      ) : null}
    </Panel>
  );
}

/**
 * The Channel B side: the deadline that exists, and the reminders that fired.
 *
 * `slaDueAt` is the *support* SLA and is labelled as that. The workflow's own
 * business-calendar deadline is workflow input and is not published, and
 * relabelling this one as it would put a number next to a name that does not
 * describe it.
 */
function ChannelBPanel({ detail }: { detail: CaseProjection }) {
  // The `support` block is the projection's home for the Channel B link;
  // `channelBWorkItemId` was the case document's.
  const workItemId = detail.support?.workItemId ?? null;

  const workItem = useQuery({
    queryKey: ["support", "work-item", workItemId],
    queryFn: workItemId === null ? skipToken : () => supportApi.readWorkItem(workItemId),
  });
  const messages = useQuery({
    queryKey: ["support", "messages", workItemId],
    queryFn: workItemId === null ? skipToken : () => supportApi.listMessages(workItemId),
  });

  if (workItemId === null) {
    return (
      <Panel title="Channel B">
        <p className="text-[11px] text-on-surface-variant">
          No support request has been raised for this case, so there is no support deadline and
          no reminder cadence.
        </p>
      </Panel>
    );
  }

  return (
    <Panel title="Channel B">
      {workItem.error !== null ? (
        <p role="alert" className="text-[11px] text-error">
          {workItem.error.message}
        </p>
      ) : workItem.isPending ? (
        <p className="text-[11px] text-on-surface-variant">Loading...</p>
      ) : (
        <dl className="flex flex-col gap-1 text-[11px]">
          <Field label="Work item" value={workItem.data.id} />
          <Field label="Status" value={workItem.data.status} />
          <Field label="Queue" value={workItem.data.queue} />
          <Field label="Assigned" value={workItem.data.assignedTo} />
          <Field label="Support SLA due" value={workItem.data.slaDueAt} />
          <Field label="Version" value={workItem.data.version} />
        </dl>
      )}
      <ReminderSummary messages={messages.data ?? null} error={messages.error} />
      <NotPublished
        what="Workflow deadline and reminder count"
        because={
          "ReturnCaseTimings -- the business-calendar wait, the reminder interval and the cap -- "
          + "is workflow input. The support SLA above is a real deadline and is a different one."
        }
      />
    </Panel>
  );
}

/**
 * Reminders, counted from the thread rather than from a field.
 *
 * `SupportMessage.businessPayload.reminderKey` is what the reminder activity
 * writes, so a reminder is visible as a message on the thread. This counts what
 * actually reached Support, which is the observable half of the cadence.
 */
function ReminderSummary({
  messages,
  error,
}: {
  messages: readonly SupportMessage[] | null;
  error: Error | null;
}) {
  if (error !== null) {
    return (
      <p role="alert" className="text-[11px] text-error">
        {error.message}
      </p>
    );
  }
  if (messages === null) {
    return <p className="text-[11px] text-on-surface-variant">Loading reminders...</p>;
  }
  const reminders = messages.filter((message) => "reminderKey" in message.businessPayload);
  if (reminders.length === 0) {
    return <p className="text-[11px] text-on-surface-variant">No reminder has been sent.</p>;
  }
  const last = reminders[reminders.length - 1];
  return (
    <dl className="flex flex-col gap-1 text-[11px]">
      <Field label="Reminders sent" value={reminders.length} />
      <Field label="Last reminder" value={last.createdAt} />
    </dl>
  );
}

/**
 * What this principal may do to this case, and where.
 *
 * Derived from the capability the route enforces and from the case's own state,
 * not from an invented "actions" endpoint -- there is none. An act that this
 * case's state makes impossible is listed as unavailable with the reason, which
 * is more useful than a hidden button.
 */
function InterventionPanel({ detail }: { detail: CaseProjection }) {
  const { can } = useCapabilities();
  const numbered = (detail.returnRecords ?? []).filter(
    (record) => record.returnReference !== null,
  ).length;
  const workItemId = detail.support?.workItemId ?? null;

  const interventions = [
    {
      name: "Answer the support request",
      capability: "returns.support.act" as const,
      route: "POST /api/v1/return-support/work-items/{id}/return-outcome",
      blocked:
        workItemId === null ? "No support request has been raised for this case." : null,
    },
    {
      name: "Record or correct a shipment",
      capability: "returns.logistics.act" as const,
      route: "POST /api/return-shipments/{rma}/updates",
      blocked: numbered === 0 ? "No RMA on this case has a reference to address." : null,
    },
    {
      name: "Reply on the support thread",
      capability: "returns.session.write" as const,
      route: "POST /api/v1/return-support/work-items/{id}/messages",
      blocked: workItemId === null ? "No support thread exists for this case." : null,
    },
  ];

  return (
    <Panel
      title="Permitted interventions"
      note="Presentation only. Every route below re-checks the capability server-side."
    >
      <ul className="flex flex-col gap-1.5 text-[11px]">
        {interventions.map((intervention) => {
          const granted = can(intervention.capability);
          const available = granted && intervention.blocked === null;
          return (
            <li key={intervention.name} className="flex min-w-0 flex-col gap-0.5">
              <span className="flex flex-wrap items-baseline gap-2">
                <span className={available ? "text-on-surface" : "text-outline"}>
                  {intervention.name}
                </span>
                <span className="text-outline">{available ? "available" : "unavailable"}</span>
              </span>
              <span className="min-w-0 break-all font-mono text-outline">
                {intervention.route}
              </span>
              {granted ? null : (
                <span className="text-on-surface-variant">
                  Requires {intervention.capability}, which you do not hold.
                </span>
              )}
              {intervention.blocked === null ? null : (
                <span className="text-on-surface-variant">{intervention.blocked}</span>
              )}
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

/**
 * What the platform currently believes about this case, with the provenance.
 *
 * **This was the append-only log and now it is not, and the title says so.**
 * `CaseProjection.facts` is the backend's `latest_case_facts` -- one entry per
 * name, the newest -- so a superseded value is no longer here. `supersedesFactId`
 * still is, which is what makes a corrected fact readable as a correction rather
 * than as the only thing ever said.
 *
 * The whole log has no route. Serving `latest_case_facts` is what plan sect. 6.3
 * says deletes the console's client-side duplicate, and it does; the log itself
 * was never part of the projection contract, and this panel names its absence
 * rather than quietly rendering the shorter list under the old heading.
 *
 * `/api/config/audit` is still not queried here: platform-scoped promotions and
 * source edits answer a different question.
 */
function AuditPanel({ facts }: { facts: readonly CaseFactProjection[] | null }) {
  const ordered = useMemo(
    () =>
      [...(facts ?? [])].sort((left, right) =>
        (right.recordedAt ?? "").localeCompare(left.recordedAt ?? ""),
      ),
    [facts],
  );

  return (
    <Panel
      title={`Case facts (${String(ordered.length)})`}
      note="The latest value of each fact, newest first. Not the append-only log: no route publishes it, and a superseded value is no longer shown."
    >
      {facts === null ? (
        <p className="text-[11px] text-on-surface-variant">
          The platform has not computed any facts for this case.
        </p>
      ) : ordered.length === 0 ? (
        <p className="text-[11px] text-on-surface-variant">Nothing has been recorded yet.</p>
      ) : (
        <ol className="flex flex-col gap-1.5 text-[11px]">
          {ordered.map((entry) => (
            <li
              key={entry.factId}
              className="flex min-w-0 flex-col gap-0.5 rounded bg-surface-container-low px-2 py-1.5"
            >
              <span className="flex flex-wrap items-baseline gap-2">
                <span className="min-w-0 break-all font-mono text-on-surface">
                  {entry.factName}
                </span>
                <span className="text-outline">{entry.recordedAt}</span>
              </span>
              <span className="min-w-0 break-all text-on-surface-variant">
                {formatValue(entry.value)}
              </span>
              <span className="flex flex-wrap items-baseline gap-2 text-outline">
                <span>{entry.agentId}</span>
                <span>{entry.channel}</span>
                {/* STATED / OBSERVED / DERIVED -- how the platform came to
                    believe it, which is the difference between an agent's
                    decision and a customer's claim. */}
                <span>{entry.acquisitionMethod}</span>
                {entry.sourceSystem === null ? null : <span>{entry.sourceSystem}</span>}
                {entry.supersedesFactId === null ? null : <span>supersedes earlier</span>}
              </span>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}

/** A fact value is `Any` on the wire; rendered without pretending it is a string. */
function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

import { useMemo, useState } from "react";
import { skipToken, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { requireDomain, type SUPPORT_SECTIONS } from "../registry";
import { useDomainSection } from "../useDomainSection";
import { RmaTicketsPage } from "./RmaTicketsPage";

/** Derived from the registry, so a section renamed there is an error here. */
type SupportSection = (typeof SUPPORT_SECTIONS)[number];
import { Bot, Inbox, PackageCheck, Plus, Send, Trash2, Truck, UserRound } from "lucide-react";

import {
  bayRecommendation,
  casesApi,
  hasBayResult,
  projectedFactString,
  type ApprovedItemProjection,
  type BayRecommendation,
  type CaseProjection,
  type ReturnRecordProjection,
  type SelectedItemProjection,
} from "../../api/cases";
import {
  nowWithOffset,
  returnShipmentsApi,
  ShipmentGraphSyncFailed,
  TRACKING_TYPES,
  type ShipmentUpdateResult,
  type TrackingType,
} from "../../api/returnShipments";
import {
  newSupportEventId,
  supportApi,
  type ReturnOutcomeRecordInput,
  type SupportMessage,
  type SupportWorkItem,
} from "../../api/support";
import { useCapabilities } from "../../hooks/capabilityContext";
import { DomainRail, RailFact, RailNote, RailSection } from "../DomainRail";
import { useElapsedSeconds } from "../../hooks/useElapsedSeconds";
import { formatDuration } from "../../format/datetime";
import { readSlaDue } from "../../format/sla";
import { readReturnStatus } from "../../format/returnStatus";

/**
 * UI-03 -- the Support console.
 *
 * Channel B: where the platform talks to Returns Support, and where a person
 * plays the Support role while Teams is not connected.
 *
 * **Three panes now, not two.** The earlier version argued a third pane would
 * only restate what the agent's request already said. That stopped being true
 * when the case grew a shape the request cannot carry: contract C3 is
 * `Case -> N return records -> N items`, each record owning its own label,
 * tracking and return location, and none of that is in a message. The third
 * pane is the case itself.
 *
 * **The RMA form is repeatable, because one reply issues N RMAs.** It used to
 * collect one flat set of fields and post `records: [record]`, which made the
 * multi-RMA half of the contract unreachable from the only screen that issues
 * RMAs -- a return split across two labels going to two locations could be
 * *described* by the backend and not *entered* anywhere.
 *
 * **`label`, `tracking` and `returnLocation` are never shown on the case.** They
 * are columns of `dbo.return_record` and the SQL schema enforces that; a case
 * header field for "the tracking number" would be a lie the moment a second RMA
 * existed.
 */

const QUEUES = [
  { label: "Open", value: "" },
  { label: "New", value: "NEW" },
  { label: "In progress", value: "IN_PROGRESS" },
  { label: "Completed", value: "COMPLETED" },
] as const;

export function SupportConsolePage() {
  const { can } = useCapabilities();
  // The section comes from the URL and the sidebar sets it, so this screen
  // holds no navigation state of its own -- the same arrangement Configuration
  // uses.
  const section = useDomainSection(requireDomain("/support")) as SupportSection;
  const client = useQueryClient();
  const [queue, setQueue] = useState<string>("");
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const workItems = useQuery({
    queryKey: ["support", "work-items", queue],
    queryFn: () => supportApi.listWorkItems(queue),
    enabled: can("returns.session.read"),
  });

  const detail = useQuery({
    queryKey: ["support", "work-item", selected],
    queryFn: selected === null ? skipToken : () => supportApi.readWorkItem(selected),
  });

  const messages = useQuery({
    queryKey: ["support", "messages", selected],
    queryFn: selected === null ? skipToken : () => supportApi.listMessages(selected),
  });

  const caseId = detail.data?.caseId ?? null;

  /**
   * The case behind the request.
   *
   * A work item without a `caseId` belongs to a return *session* rather than a
   * case, which is a real and different thing -- the outcome endpoint 409s for
   * one -- so this stays unfetched rather than being asked for and 404ing.
   */
  const caseDetail = useQuery({
    queryKey: ["cases", caseId],
    queryFn: caseId === null ? skipToken : () => casesApi.readProjection(caseId),
  });

  const reply = useMutation({
    mutationFn: (text: string) => {
      if (selected === null || detail.data === undefined) {
        throw new Error("Select a request before replying.");
      }
      return supportApi.reply(selected, {
        messageText: text,
        expectedVersion: detail.data.version,
      });
    },
    onSuccess: async () => {
      setDraft("");
      // Both: the thread gained a message and the work item gained a version,
      // and replying against the old one would be refused.
      await client.invalidateQueries({ queryKey: ["support", "messages", selected] });
      await client.invalidateQueries({ queryKey: ["support", "work-item", selected] });
    },
  });

  if (!can("returns.session.read")) {
    return <p className="text-sm text-on-surface-variant">You do not have access to support.</p>;
  }

  // A different job on the same domain, so it is a section rather than a
  // branch inside the queue: folding RMA creation into the queue would have
  // made the queue's own filters navigation for something else.
  if (section === "RMA Tickets") {
    return <RmaTicketsPage />;
  }

  return (
    <div className="grid h-[calc(100vh-8rem)] grid-cols-1 gap-4 lg:grid-cols-[minmax(0,4fr)_minmax(0,6fr)] xl:grid-cols-[minmax(0,3fr)_minmax(0,5fr)_minmax(0,6fr)]">
      {/*
        The deadline and the shape of the open request, which are the two things
        the panes push off-screen: the SLA sits in the case pane's Channel B
        block and the RMA count needs scrolling to reach.
      */}
      <DomainRail>
        <RailSection title="Queue">
          {workItems.error !== null ? (
            <RailNote>The queue could not be read.</RailNote>
          ) : workItems.isPending ? (
            <RailNote>Loading...</RailNote>
          ) : (
            <>
              <RailFact label="Filter" value={queue === "" ? "Open" : queue} />
              <RailFact label="Waiting" value={workItems.data.length} />
            </>
          )}
        </RailSection>
        <RailSection title="Open request">
          {detail.data === undefined ? (
            <RailNote>Nothing selected.</RailNote>
          ) : (
            <>
              <RailFact label="Status" value={detail.data.status} />
              {/* The support SLA, named as that. The workflow's own
                  business-calendar deadline is workflow input and is not
                  published, so this is not relabelled as it. */}
              <RailFact label="Support SLA due" value={readSlaDue(detail.data.slaDueAt).text} />
              <RailFact label="Assigned" value={detail.data.assignedTo} />
              {caseId === null ? (
                <RailNote>Session-backed, so it has no case and no RMAs.</RailNote>
              ) : (
                <RailFact
                  label="RMAs"
                  value={
                    // `null` for "not read yet" and `null` again for "the
                    // platform has not computed the records". Both are the
                    // absence this rail already renders as a dash; what must
                    // not happen is either becoming a confident `0`.
                    caseDetail.data?.returnRecords?.length ?? null
                  }
                />
              )}
              {(caseDetail.data?.selectedItems?.length ?? 0) === 0 ? null : (
                <RailFact
                  label="Lines unassigned"
                  value={caseDetail.data?.selectedItems?.length ?? null}
                />
              )}
            </>
          )}
        </RailSection>
      </DomainRail>
      <QueuePane
        queue={queue}
        onQueueChange={setQueue}
        items={workItems.data ?? []}
        error={workItems.error}
        loading={workItems.isPending}
        selected={selected}
        onSelect={(id) => {
          setSelected(id);
          setDraft("");
        }}
      />
      <ThreadPane
        item={detail.data ?? null}
        messages={messages.data ?? []}
        loading={selected !== null && messages.isPending}
        draft={draft}
        onDraftChange={setDraft}
        onSend={() => {
          if (draft.trim().length > 0) reply.mutate(draft.trim());
        }}
        sending={reply.isPending}
        error={reply.error}
      />
      <CasePane
        workItemId={selected}
        item={detail.data ?? null}
        caseDetail={caseDetail.data ?? null}
        loading={caseId !== null && caseDetail.isPending}
        error={caseDetail.error}
        canAct={can("returns.support.act")}
        canLogistics={can("returns.logistics.act")}
      />
    </div>
  );
}

function Pane({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-outline-variant bg-surface-container-lowest">
      <h2 className="border-b border-outline-variant px-4 py-3 text-sm font-semibold text-on-surface">
        {title}
      </h2>
      {children}
    </section>
  );
}

/** A label/value line that survives a 200-character reference without pushing the pane wide. */
function Field({ label, value }: { label: string; value: string | number | null }) {
  if (value === null || value === "") return null;
  return (
    <div className="flex min-w-0 gap-1.5">
      <dt className="shrink-0 text-outline">{label}</dt>
      <dd className="min-w-0 break-all text-on-surface">{String(value)}</dd>
    </div>
  );
}

function QueuePane({
  queue,
  onQueueChange,
  items,
  error,
  loading,
  selected,
  onSelect,
}: {
  queue: string;
  onQueueChange: (value: string) => void;
  items: readonly SupportWorkItem[];
  error: Error | null;
  loading: boolean;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <Pane title="Return requests">
      <div className="flex flex-wrap gap-1.5 border-b border-outline-variant px-3 py-2">
        {QUEUES.map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => { onQueueChange(option.value); }}
            className={`rounded-full border px-3 py-1 text-xs transition ${
              queue === option.value
                ? "border-primary text-primary"
                : "border-outline-variant text-on-surface-variant hover:border-primary hover:text-primary"
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto">
        {/*
          Three states, not two. `data ?? []` would tell an operator their queue
          is empty when the truth is that we could not ask -- and an empty
          Support queue is exactly the thing they would act on by going home.
        */}
        {error !== null ? (
          <p role="alert" className="px-4 py-3 text-sm text-error">
            {error.message}
          </p>
        ) : loading ? (
          <p className="px-4 py-3 text-sm text-on-surface-variant">Loading...</p>
        ) : items.length === 0 ? (
          <p className="flex items-center gap-2 px-4 py-3 text-sm text-on-surface-variant">
            <Inbox size={15} aria-hidden="true" />
            Nothing waiting in this queue.
          </p>
        ) : (
          <ul>
            {items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => { onSelect(item.id); }}
                  className={`flex w-full flex-col gap-0.5 border-b border-outline-variant px-4 py-2.5 text-left transition hover:bg-surface-container ${
                    item.id === selected ? "bg-surface-container" : ""
                  }`}
                >
                  <span className="truncate text-sm text-on-surface">{item.subject}</span>
                  <span className="flex min-w-0 items-center gap-2 text-[11px] text-outline">
                    <span className="shrink-0">{item.status}</span>
                    {item.returnReference !== null ? (
                      <span className="truncate">{item.returnReference}</span>
                    ) : null}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Pane>
  );
}

function ThreadPane({
  item,
  messages,
  loading,
  draft,
  onDraftChange,
  onSend,
  sending,
  error,
}: {
  item: SupportWorkItem | null;
  messages: readonly SupportMessage[];
  loading: boolean;
  draft: string;
  onDraftChange: (value: string) => void;
  onSend: () => void;
  sending: boolean;
  error: Error | null;
}) {
  if (item === null) {
    return (
      <Pane title="Conversation">
        <div className="flex flex-1 items-center justify-center p-6 text-center">
          <p className="max-w-xs text-sm text-on-surface-variant">
            Pick a return request to read the conversation and reply.
          </p>
        </div>
      </Pane>
    );
  }

  return (
    <Pane title={item.subject}>
      <dl className="flex flex-wrap gap-x-6 gap-y-1 border-b border-outline-variant px-4 py-2 text-[11px]">
        <Field label="Status" value={item.status} />
        <Field label="Assigned" value={item.assignedTo} />
      </dl>

      <div className="flex-1 overflow-y-auto p-4">
        {loading ? (
          <p className="text-sm text-on-surface-variant">Loading...</p>
        ) : (
          <ol className="flex flex-col gap-4">
            {messages.map((message) => (
              <Message key={message.id} message={message} />
            ))}
          </ol>
        )}
      </div>

      <form
        className="border-t border-outline-variant p-3"
        onSubmit={(event) => {
          event.preventDefault();
          onSend();
        }}
      >
        {error !== null ? (
          // Verbatim: a version conflict and an authorization failure need
          // different responses, and flattening them loses which one it was.
          <p role="alert" className="mb-2 text-sm text-error">
            {error.message}
          </p>
        ) : null}
        <div className="relative flex items-center">
          <input
            aria-label="Reply to the return request"
            value={draft}
            onChange={(event) => { onDraftChange(event.target.value); }}
            placeholder="Reply to the agent..."
            className="w-full rounded-lg border border-outline-control bg-surface py-2.5 pl-3 pr-11 text-sm text-on-surface outline-none transition placeholder:text-outline focus:border-primary focus:ring-1 focus:ring-primary"
          />
          <button
            type="submit"
            aria-label="Send reply"
            disabled={draft.trim().length === 0 || sending}
            className="absolute right-2 flex size-8 items-center justify-center rounded bg-primary text-on-primary transition disabled:opacity-40"
          >
            <Send size={15} />
          </button>
        </div>
      </form>
    </Pane>
  );
}

/**
 * The case: what it is, where it is going, and the RMAs that carry it.
 *
 * Everything RMA-scoped is inside an RMA block. Nothing RMA-scoped appears in
 * the case header, because the header is the one place a second RMA would make
 * a field wrong without changing it.
 */
function CasePane({
  workItemId,
  item,
  caseDetail,
  loading,
  error,
  canAct,
  canLogistics,
}: {
  workItemId: string | null;
  item: SupportWorkItem | null;
  caseDetail: CaseProjection | null;
  loading: boolean;
  error: Error | null;
  canAct: boolean;
  canLogistics: boolean;
}) {
  // `CaseProjection.facts` is the backend's own `latest_case_facts` -- one entry
  // per name, already the newest. The client-side reduction that used to happen
  // here is gone with it.
  const bay = useMemo(() => bayRecommendation(caseDetail?.facts), [caseDetail?.facts]);
  const fulfillment = useMemo(
    () => ({
      status: projectedFactString(caseDetail?.facts, "fulfillment_status"),
      evidence: projectedFactString(caseDetail?.facts, "shipment_evidence"),
    }),
    [caseDetail?.facts],
  );

  if (item === null) {
    return (
      <Pane title="Case">
        <div className="flex flex-1 items-center justify-center p-6 text-center">
          <p className="max-w-xs text-sm text-on-surface-variant">
            The case, its RMAs and where the goods are going appear here.
          </p>
        </div>
      </Pane>
    );
  }

  if (item.caseId === null) {
    // Not an error. A session-backed work item is a different, older shape, and
    // the outcome endpoint answers 409 for one rather than 404 -- so saying
    // "no case" beats showing an empty RMA list.
    return (
      <Pane title="Case">
        <div className="flex-1 overflow-y-auto p-4">
          <p className="text-sm text-on-surface-variant">
            This request belongs to a return session rather than a case, so it has no RMAs to
            show. Record its outcome through the support action instead.
          </p>
        </div>
      </Pane>
    );
  }

  if (error !== null) {
    return (
      <Pane title="Case">
        <p role="alert" className="px-4 py-3 text-sm text-error">
          {error.message}
        </p>
      </Pane>
    );
  }

  if (loading || caseDetail === null) {
    return (
      <Pane title="Case">
        <p className="px-4 py-3 text-sm text-on-surface-variant">Loading...</p>
      </Pane>
    );
  }

  // `null` (not computed) and `[]` (computed, and there are none) render the
  // same here on purpose: from Support's seat both mean "no RMA to work on".
  // The distinction is kept where it decides something -- the rail's counts.
  const records = caseDetail.returnRecords ?? [];
  const selectedItems = caseDetail.selectedItems ?? [];

  return (
    <Pane title="Case">
      <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-4">
        <CaseStatusPanel detail={caseDetail} fulfillment={fulfillment} />
        <BayPanel bay={bay} />

        <section className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-outline">
            RMAs ({records.length})
          </h3>
          {records.length === 0 ? (
            <p className="text-sm text-on-surface-variant">
              No RMA has been issued for this case yet.
            </p>
          ) : (
            records.map((record) => (
              <RmaBlock
                key={record.returnRecordId}
                record={record}
                canLogistics={canLogistics}
              />
            ))
          )}
        </section>

        {selectedItems.length > 0 ? (
          <section className="flex flex-col gap-1.5">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-outline">
              Lines no RMA covers yet ({selectedItems.length})
            </h3>
            <ItemList items={selectedItems.map(selectedRow)} />
          </section>
        ) : null}

        {workItemId === null ? null : (
          <IssueOutcomeForm
            workItemId={workItemId}
            caseId={item.caseId}
            unassignedItems={selectedItems}
            suggestedReturnLocation={bay.returnLocation}
            canAct={canAct}
          />
        )}
      </div>
    </Pane>
  );
}

/**
 * What the case is, and what it is still waiting for.
 *
 * **`awaiting` replaces the persistence block that used to sit here.** The
 * projection carries no `workflowId`, `sessionId`, `configurationReleaseId` or
 * `graphGenerationId` -- deliberately: those describe how the platform stored
 * and synced the case, not what the return is, and `CaseProjection` is the
 * business read. What Support actually needed from them was the one question
 * they were being used to approximate -- is anything going to happen to this
 * case -- and `awaiting`, `stage` and `businessComplete` answer it directly and
 * from a computation rather than an inference.
 *
 * A case awaiting nothing while not business-complete is the state that used to
 * be read off a missing `workflowId`, and it is said here in those words.
 */
function CaseStatusPanel({
  detail,
  fulfillment,
}: {
  detail: CaseProjection;
  fulfillment: { status: string | null; evidence: string | null };
}) {
  const orderReference = detail.confirmedOrder?.orderReference ?? null;
  return (
    <section className="flex flex-col gap-2 rounded-md border border-outline-variant p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full bg-surface-container px-2 py-0.5 text-[11px] text-on-surface">
          {detail.status}
        </span>
        <span className="rounded-full bg-surface-container px-2 py-0.5 text-[11px] text-on-surface-variant">
          {detail.stage}
        </span>
        {orderReference === null ? null : (
          <span className="min-w-0 break-all font-mono text-[11px] text-on-surface-variant">
            {orderReference}
          </span>
        )}
      </div>
      <dl className="flex flex-col gap-1 text-[11px]">
        <Field label="Case" value={detail.caseId} />
        <Field label="Fulfilment" value={fulfillment.status} />
        <Field label="Shipment evidence" value={fulfillment.evidence} />
      </dl>
      {/*
        Computed by the backend from the return method's requirement table, not
        inferred here. An empty `awaiting` on a case that is not
        business-complete is the shape that means nothing is going to move on
        its own -- which is what an absent workflow used to be read as.
      */}
      {detail.awaiting.length > 0 ? (
        <p className="text-[11px] text-on-surface-variant">
          Waiting on {detail.awaiting.join(", ")}.
        </p>
      ) : detail.businessComplete ? (
        <p className="text-[11px] text-on-surface-variant">
          Everything this return needs has been recorded.
        </p>
      ) : (
        <p className="text-[11px] text-on-surface-variant">
          Nothing is outstanding and the return is not complete
          {detail.isTerminal ? ", and the case is finished" : ""}.
        </p>
      )}
      <details className="text-[11px]">
        <summary className="cursor-pointer text-outline">Revision</summary>
        <dl className="mt-1.5 flex flex-col gap-1">
          <Field label="Case revision" value={detail.revision} />
          <Field label="Updated" value={detail.updatedAt} />
          <Field label="Confirmation key" value={detail.confirmedOrder?.confirmationKey ?? null} />
          <Field label="Support work item" value={detail.support?.workItemId ?? null} />
        </dl>
      </details>
    </section>
  );
}

/**
 * The bay recommendation, and the fact that not having one is normal.
 *
 * Bay placement is best-effort by declared policy: the workflow asks for it,
 * proceeds without it, and records `bay_reason` when it could not answer. A
 * case with no bay is therefore a state, not a failure, and this panel is
 * deliberately quiet about it -- an error tone here would send an operator
 * looking for a fault that is not there.
 */
function BayPanel({ bay }: { bay: BayRecommendation }) {
  const resolved = hasBayResult(bay);
  return (
    <section className="flex flex-col gap-1.5 rounded-md border border-outline-variant p-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-outline">
        Bay recommendation
      </h3>
      {resolved ? (
        <dl className="flex flex-col gap-1 text-[11px]">
          <Field label="Warehouse" value={bay.warehouseReference} />
          <Field label="Bay" value={bay.bayReference} />
          <Field label="Return to" value={bay.returnLocation} />
          <Field
            label="Confidence"
            value={
              // Computed per case (contract C2), so it is shown as what it is
              // rather than rounded into a badge that would read the same for
              // 0.51 and 0.99.
              bay.confidence === null ? null : `${(bay.confidence * 100).toFixed(1)}%`
            }
          />
          <Field label="Reason" value={bay.reason} />
          <Field label="Evidence" value={bay.evidenceReference} />
          <Field label="Capacity" value={bay.capacityEvidence} />
        </dl>
      ) : (
        <div className="flex flex-col gap-1 text-[11px] text-on-surface-variant">
          <p>
            No bay recommended. Placement is best-effort -- the case proceeds without one, and
            Support may set the return location on each RMA below.
          </p>
          {bay.reason === null ? null : (
            <dl>
              <Field label="Reason" value={bay.reason} />
            </dl>
          )}
        </div>
      )}
    </section>
  );
}

/**
 * One RMA: its own label, tracking, return location, and the items it covers.
 *
 * A repeatable block rather than a row, because the shipment editor belongs
 * *inside* it. The endpoint the editor calls is keyed on the RMA
 * (`/api/return-shipments/{return_reference}/updates`) and a case-level
 * shipment control would have no reference to send.
 */
function RmaBlock({
  record,
  canLogistics,
}: {
  record: ReturnRecordProjection;
  canLogistics: boolean;
}) {
  // The live label: `active` and not superseded, never `artifacts[0]`. A
  // replaced label stays on the record for audit, and picking the first one
  // would print the one somebody replaced.
  const label =
    record.artifacts?.find(
      (artifact) =>
        artifact.artifactType === "SHIPPING_LABEL"
        && artifact.active === true
        && artifact.supersededBy === null,
    ) ?? null;
  const shipments = record.shipments ?? [];
  const approvedItems = record.approvedItems ?? [];
  return (
    <article className="flex flex-col gap-2 rounded-md border border-outline-variant p-3">
      <div className="flex flex-wrap items-center gap-2">
        <PackageCheck size={14} aria-hidden="true" className="text-on-surface-variant" />
        <span className="min-w-0 break-all text-sm font-medium text-on-surface">
          {record.returnReference ?? "RMA not yet numbered"}
        </span>
        <span className="rounded-full bg-surface-container px-2 py-0.5 text-[11px] text-on-surface-variant">
          {readReturnStatus(record.status)}
        </span>
      </div>
      <dl className="flex flex-col gap-1 text-[11px]">
        <Field label="Label" value={label?.artifactId ?? null} />
        <Field label="Method" value={record.returnMethod} />
        <Field label="Return to" value={record.returnLocation} />
      </dl>

      {/*
        Packages, each with its own tracking number. A label with no package is
        the real shape of record `4e372a39...` -- the label is above, attributed
        to nothing, and this says the parcel has not been tendered rather than
        inventing one to hang the label on.
      */}
      {shipments.length === 0 ? (
        <p className="text-[11px] text-on-surface-variant">
          {label === null
            ? "No package has been tendered for this RMA."
            : "This RMA has a label and no package yet, so there is no tracking number."}
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {shipments.map((shipment) => (
            <li
              key={shipment.shipmentId}
              className="flex min-w-0 flex-wrap items-baseline gap-x-2 rounded bg-surface-container-low px-2 py-1 text-[11px]"
            >
              <span className="min-w-0 break-all font-mono text-on-surface">
                {shipment.trackingNumber ?? "no tracking number"}
              </span>
              {shipment.shipmentStatus === null ? null : (
                <span className="text-outline">{shipment.shipmentStatus}</span>
              )}
              {shipment.carrier === null ? null : (
                <span className="text-outline">{shipment.carrier}</span>
              )}
            </li>
          ))}
        </ul>
      )}

      {approvedItems.length === 0 ? (
        <p className="text-[11px] text-on-surface-variant">No lines are attached to this RMA.</p>
      ) : (
        <ItemList items={approvedItems.map(approvedRow)} />
      )}

      {record.returnReference === null ? (
        <p className="text-[11px] text-on-surface-variant">
          A shipment update needs an RMA number, which this record does not have yet.
        </p>
      ) : (
        <ShipmentEditor returnReference={record.returnReference} canLogistics={canLogistics} />
      )}
    </article>
  );
}

/**
 * One line, however it reached the screen.
 *
 * `SelectedItemProjection` and `ApprovedItemProjection` are different shapes --
 * a line the associate named versus a line an RMA authorized -- and they are
 * deliberately not merged on the contract. They render identically, so the two
 * mappers below are where the difference stops, rather than a union type that
 * would let a caller read a reason off an approved line that has none.
 */
type ItemRow = {
  readonly returnItemId: string;
  readonly orderLineReference: string;
  readonly productReference: string | null;
  readonly quantity: number | null;
  readonly reason: string | null;
  readonly condition: string | null;
};

function selectedRow(item: SelectedItemProjection): ItemRow {
  return {
    returnItemId: item.returnItemId,
    orderLineReference: item.orderLineReference,
    productReference: item.productReference,
    quantity: item.quantity,
    reason: item.reason,
    condition: item.condition,
  };
}

function approvedRow(item: ApprovedItemProjection): ItemRow {
  return {
    returnItemId: item.returnItemId,
    orderLineReference: item.orderLineReference ?? item.returnItemId,
    productReference: item.productReference,
    // The quantity the RMA *authorized*, which is not always the quantity asked
    // for, and is labelled as the RMA's below.
    quantity: item.quantityApproved,
    // An approved line carries a disposition, not a customer's reason.
    reason: item.disposition,
    condition: item.itemStatus,
  };
}

function ItemList({ items }: { items: readonly ItemRow[] }) {
  return (
    <ul className="flex flex-col gap-1">
      {items.map((line) => (
        <li
          key={line.returnItemId}
          className="flex min-w-0 flex-wrap items-baseline gap-x-2 rounded bg-surface-container-low px-2 py-1 text-[11px]"
        >
          <span className="min-w-0 break-all font-mono text-on-surface">
            {line.orderLineReference}
          </span>
          {line.quantity === null ? null : <span className="text-outline">x{line.quantity}</span>}
          {line.productReference === null ? null : (
            <span className="min-w-0 break-all text-on-surface-variant">
              {line.productReference}
            </span>
          )}
          {line.reason === null ? null : <span className="text-outline">{line.reason}</span>}
          {line.condition === null ? null : <span className="text-outline">{line.condition}</span>}
        </li>
      ))}
    </ul>
  );
}

/**
 * Record a carrier observation against one RMA, and correct one.
 *
 * **This is the correction path.** An RMA's shipment state is the one thing on
 * this screen that stays correctable after Support has answered: the store
 * orders updates by `statusAt` and nothing else, so a later observation
 * supersedes an earlier one, and an earlier one submitted late is refused as
 * `STALE` rather than quietly rewriting history. Support's *outcome*, by
 * contrast, is first-response-wins in the workflow -- which is why the RMA form
 * below says so instead of offering an edit that would do nothing.
 *
 * **All three outcomes are 200 and none is styled as a failure.** `DUPLICATE`
 * means the platform already held this exact observation; `STALE` means it
 * holds a newer one and names it. Rendering either in red would train an
 * operator to re-enter a correct submission with a fresh timestamp, which is
 * how a retry becomes a second history.
 */
function ShipmentEditor({
  returnReference,
  canLogistics,
}: {
  returnReference: string;
  canLogistics: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [trackingReference, setTrackingReference] = useState("");
  const [shipmentStatus, setShipmentStatus] = useState("");
  const [trackingType, setTrackingType] = useState<TrackingType>("PPL");
  const [carrierCode, setCarrierCode] = useState("");
  const [shipmentDetails, setShipmentDetails] = useState("");
  const [statusAt, setStatusAt] = useState(() => nowWithOffset());

  const update = useMutation({
    mutationFn: () =>
      returnShipmentsApi.recordUpdate(returnReference, {
        trackingReference: trackingReference.trim(),
        shipmentStatus: shipmentStatus.trim(),
        statusAt,
        trackingType,
        // Omitted rather than sent empty: "" would be stored as a carrier code
        // that exists and is blank.
        ...(carrierCode.trim() === "" ? {} : { carrierCode: carrierCode.trim() }),
        ...(shipmentDetails.trim() === "" ? {} : { shipmentDetails: shipmentDetails.trim() }),
      }),
  });
  const recordingElapsed = useElapsedSeconds(update.isPending);

  const ready = trackingReference.trim().length > 0 && shipmentStatus.trim().length > 0;

  if (!canLogistics) {
    return (
      <p className="text-[11px] text-on-surface-variant">
        Recording a shipment requires returns.logistics.act, which you do not hold.
      </p>
    );
  }

  if (!open) {
    return (
      <div className="flex flex-col gap-1">
        <button
          type="button"
          onClick={() => {
            update.reset();
            // Re-stamped on open rather than held from mount: a form opened an
            // hour after the page loaded would otherwise submit an hour-old
            // observation and be answered STALE for no reason the operator can
            // see.
            setStatusAt(nowWithOffset());
            setOpen(true);
          }}
          className="flex items-center gap-1.5 self-start text-xs text-primary transition hover:underline"
        >
          <Truck size={14} aria-hidden="true" />
          Record or correct a shipment
        </button>
        <ShipmentOutcome result={update.data ?? null} error={update.error} />
      </div>
    );
  }

  return (
    <form
      className="flex flex-col gap-2 rounded border border-outline-variant p-2.5"
      onSubmit={(event) => {
        event.preventDefault();
        if (!ready || update.isPending) return;
        update.mutate();
      }}
    >
      <ShipmentOutcome result={update.data ?? null} error={update.error} />
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <TextField
          label="Tracking number"
          value={trackingReference}
          onChange={setTrackingReference}
          required
        />
        <TextField
          label="Carrier status"
          value={shipmentStatus}
          onChange={setShipmentStatus}
          required
        />
        <label className="flex flex-col gap-1 text-[11px] text-outline">
          Ship via
          <select
            value={trackingType}
            onChange={(event) => { setTrackingType(event.target.value as TrackingType); }}
            className="rounded border border-outline-control bg-surface px-2 py-1.5 text-sm text-on-surface outline-none transition focus:border-primary focus:ring-1 focus:ring-primary"
          >
            {TRACKING_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </label>
        <TextField label="Carrier code" value={carrierCode} onChange={setCarrierCode} />
        <label className="flex flex-col gap-1 text-[11px] text-outline sm:col-span-2">
          Status timestamp
          <input
            value={statusAt}
            onChange={(event) => { setStatusAt(event.target.value); }}
            className="rounded border border-outline-control bg-surface px-2 py-1.5 font-mono text-xs text-on-surface outline-none transition focus:border-primary focus:ring-1 focus:ring-primary"
          />
          <span className="text-outline">
            Must carry a timezone offset. This decides whether the update advances the stored
            shipment or is refused as stale -- nothing else does.
          </span>
        </label>
        <TextField
          label="Details"
          value={shipmentDetails}
          onChange={setShipmentDetails}
          className="sm:col-span-2"
        />
      </div>
      <div className="flex items-center gap-2">
        <button
          type="submit"
          // Disabled while in flight. The endpoint is idempotent and would
          // answer DUPLICATE, but a double-click still costs a round trip and
          // shows the operator an outcome that is about their own second click
          // rather than about the carrier.
          disabled={!ready || update.isPending}
          className="rounded bg-primary px-3 py-1.5 text-xs text-on-primary transition disabled:opacity-40"
        >
          {update.isPending ? `Recording... ${formatDuration(recordingElapsed)}` : "Record shipment"}
        </button>
        <button
          type="button"
          // Same reason as the outcome form: closing does not stop the write,
          // and the operator would learn that only from the case changing under
          // them later.
          disabled={update.isPending}
          onClick={() => { setOpen(false); }}
          title={update.isPending ? "The write has started and cannot be stopped." : undefined}
          className="text-xs text-on-surface-variant transition hover:text-on-surface disabled:cursor-not-allowed disabled:opacity-40"
        >
          Close
        </button>
      </div>
    </form>
  );
}

/**
 * What the store decided, said in the store's own terms.
 *
 * `role="status"` for all three verdicts and `role="alert"` only for a genuine
 * failure: a screen reader should not announce "already recorded" the way it
 * announces a refusal.
 */
function ShipmentOutcome({
  result,
  error,
}: {
  result: ShipmentUpdateResult | null;
  error: Error | null;
}) {
  if (error !== null) {
    if (error instanceof ShipmentGraphSyncFailed) {
      return (
        <p role="status" className="rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface">
          The shipment was committed to the authoritative store; only the graph projection
          failed. Submitting the identical update again is safe and will answer DUPLICATE. Do
          not re-enter it with a new timestamp.
        </p>
      );
    }
    return (
      <p role="alert" className="text-[11px] text-error">
        {error.message}
      </p>
    );
  }
  if (result === null) return null;

  const explanation =
    result.outcome === "APPLIED"
      ? "Recorded. The associate's next turn reads this."
      : result.outcome === "DUPLICATE"
        ? "Already recorded. The platform held this exact observation, so nothing changed."
        : "Refused as stale. The platform holds a newer observation than the timestamp you sent.";

  return (
    <div
      role="status"
      className="flex flex-col gap-1 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface"
    >
      <span className="font-medium">
        {result.outcome} -- {explanation}
      </span>
      <dl className="flex flex-col gap-0.5">
        <Field label="Stored status" value={result.currentStatus} />
        <Field label="Stored at" value={result.currentStatusAt} />
        <Field label="Row version" value={result.rowVersion} />
        <Field label="Graph generation" value={result.graphGenerationId} />
        {result.reading === null ? null : (
          <>
            <Field label="Fulfilment" value={result.reading.fulfillmentStatus} />
            <Field label="Evidence" value={result.reading.evidenceReference} />
          </>
        )}
      </dl>
      {result.outcome === "APPLIED" && result.reading === null ? (
        <span className="text-on-surface-variant">
          No case owns this RMA, so nobody was told. The shipment row is still authoritative.
        </span>
      ) : null}
    </div>
  );
}

function TextField({
  label,
  value,
  onChange,
  required = false,
  className = "",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
  className?: string;
}) {
  return (
    <label className={`flex flex-col gap-1 text-[11px] text-outline ${className}`}>
      {label}
      {required ? <span className="sr-only">(required)</span> : null}
      <input
        value={value}
        required={required}
        onChange={(event) => { onChange(event.target.value); }}
        className="rounded border border-outline-control bg-surface px-2 py-1.5 text-sm text-on-surface outline-none transition focus:border-primary focus:ring-1 focus:ring-primary"
      />
    </label>
  );
}

/** One RMA being drafted, before it is sent. */
type DraftRecord = {
  readonly key: string;
  returnReference: string;
  trackingReference: string;
  labelReference: string;
  returnLocation: string;
  shippingInstructionReference: string;
  orderLineReferences: readonly string[];
};

function emptyDraft(returnLocation: string): DraftRecord {
  return {
    // Stable per block. Indexing by array position would make removing the
    // first of three RMAs re-key the other two, and React would carry the
    // removed block's input state into its neighbour.
    key: `rma-${String(Date.now())}-${String(Math.random()).slice(2, 8)}`,
    returnReference: "",
    trackingReference: "",
    labelReference: "",
    returnLocation,
    shippingInstructionReference: "",
    orderLineReferences: [],
  };
}

function toInput(draft: DraftRecord): ReturnOutcomeRecordInput {
  const optional = (value: string) => (value.trim() === "" ? undefined : value.trim());
  return {
    returnReference: draft.returnReference.trim(),
    ...(optional(draft.trackingReference) === undefined
      ? {}
      : { trackingReference: draft.trackingReference.trim() }),
    ...(optional(draft.labelReference) === undefined
      ? {}
      : { labelReference: draft.labelReference.trim() }),
    ...(optional(draft.returnLocation) === undefined
      ? {}
      : { returnLocation: draft.returnLocation.trim() }),
    ...(optional(draft.shippingInstructionReference) === undefined
      ? {}
      : { shippingInstructionReference: draft.shippingInstructionReference.trim() }),
    ...(draft.orderLineReferences.length === 0
      ? {}
      : { orderLineReferences: [...draft.orderLineReferences] }),
  };
}

/**
 * Issue the RMAs -- the moment Channel B answers Channel A.
 *
 * **N blocks, not one set of fields.** The endpoint has always taken
 * `records: [...]`, and the previous form posted `records: [oneRecord]`, so the
 * multi-RMA half of contract C3 was unreachable from the only screen that
 * issues RMAs. A return split across two labels going to two locations could be
 * described by the backend and entered nowhere.
 *
 * **Duplicate-submit protection is real, not decorative.** `ReturnCaseWorkflow`
 * takes the first `support_response` and ignores every later one -- so a second
 * send is not "harmless", it is *silently nothing*, which is worse than a
 * refusal because the operator watches it succeed. The form therefore disables
 * itself while a send is in flight and, once one has landed, refuses to send
 * again and says why instead of offering a button that would appear to work.
 */
function IssueOutcomeForm({
  workItemId,
  caseId,
  unassignedItems,
  suggestedReturnLocation,
  canAct,
}: {
  workItemId: string;
  caseId: string;
  unassignedItems: readonly SelectedItemProjection[];
  suggestedReturnLocation: string | null;
  canAct: boolean;
}) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [drafts, setDrafts] = useState<readonly DraftRecord[]>([]);
  /**
   * The identity of the answer being drafted, minted when the form opens.
   *
   * Held in state rather than computed at send time so that every route back to
   * the same send reuses it: a re-render, React Query re-invoking `mutationFn`
   * on retry, a double click, and -- the one that actually loses RMAs -- an
   * operator pressing send again after the response never arrived. All four
   * collapse into one Support event. Opening the form again is a different
   * business act and gets a different id, which is the distinction the backend's
   * `(caseId, supportEventId)` constraint is there to preserve.
   */
  const [supportEventId, setSupportEventId] = useState(() => newSupportEventId(workItemId));

  const outcome = useMutation({
    // The id travels in the variables, not read from state inside the request:
    // a retry re-runs `mutationFn` with the variables it was given, so this is
    // stable by construction rather than by whatever state happens to be current.
    mutationFn: (submission: {
      readonly supportEventId: string;
      readonly records: readonly ReturnOutcomeRecordInput[];
    }) =>
      supportApi.submitReturnOutcome(workItemId, {
        records: [...submission.records],
        supportEventId: submission.supportEventId,
      }),
    onSuccess: async () => {
      setOpen(false);
      // The workflow writes asynchronously, so this is optimistic about
      // timing, not about the result: a refetch that lands early shows the case
      // unchanged and the next one shows it settled.
      await client.invalidateQueries({ queryKey: ["support", "work-item", workItemId] });
      await client.invalidateQueries({ queryKey: ["support", "messages", workItemId] });
      await client.invalidateQueries({ queryKey: ["support", "work-items"] });
      await client.invalidateQueries({ queryKey: ["cases", caseId] });
    },
  });
  const elapsed = useElapsedSeconds(outcome.isPending);

  const complete = drafts.filter((draft) => draft.returnReference.trim().length > 0);
  const duplicateReferences =
    new Set(complete.map((draft) => draft.returnReference.trim())).size !== complete.length;

  if (!canAct) {
    return (
      <p className="text-[11px] text-on-surface-variant">
        Issuing an RMA requires returns.support.act, which you do not hold.
      </p>
    );
  }

  if (outcome.isSuccess) {
    return (
      <section
        role="status"
        className="flex flex-col gap-1 rounded-md border border-outline-variant p-3 text-[11px]"
      >
        <span className="text-sm text-on-surface">Answer sent to the case.</span>
        <span className="text-on-surface-variant">
          The workflow acts on the first response and ignores any later one, so this cannot be
          re-sent. Shipment details stay correctable on each RMA above; anything else needs a
          new request.
        </span>
      </section>
    );
  }

  if (!open) {
    return (
      <div className="flex flex-col gap-1 border-t border-outline-variant pt-3">
        {outcome.error === null ? null : (
          <p role="alert" className="text-[11px] text-error">
            {outcome.error.message}
          </p>
        )}
        <button
          type="button"
          onClick={() => {
            outcome.reset();
            setDrafts([emptyDraft(suggestedReturnLocation ?? "")]);
            // A fresh form is a fresh answer. Reusing the previous id here
            // would make a genuinely new reply look like a resend of the old
            // one and be rejected as an idempotency conflict.
            setSupportEventId(newSupportEventId(workItemId));
            setOpen(true);
          }}
          className="flex items-center gap-1.5 self-start text-xs text-primary transition hover:underline"
        >
          <PackageCheck size={14} aria-hidden="true" />
          Issue RMAs
        </button>
      </div>
    );
  }

  return (
    <form
      className="flex flex-col gap-3 border-t border-outline-variant pt-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (complete.length === 0 || duplicateReferences || outcome.isPending) return;
        outcome.mutate({ supportEventId, records: complete.map(toInput) });
      }}
    >
      {outcome.error === null ? null : (
        <p role="alert" className="text-[11px] text-error">
          {outcome.error.message}
        </p>
      )}
      {duplicateReferences ? (
        <p role="alert" className="text-[11px] text-error">
          Two blocks carry the same RMA number. Each block is a separate RMA.
        </p>
      ) : null}

      {drafts.map((draft, index) => (
        <DraftRecordBlock
          key={draft.key}
          draft={draft}
          index={index}
          removable={drafts.length > 1}
          unassignedItems={unassignedItems}
          claimedElsewhere={
            new Set(
              drafts.flatMap((other) =>
                other.key === draft.key ? [] : other.orderLineReferences,
              ),
            )
          }
          onChange={(next) => {
            setDrafts((current) =>
              current.map((entry) => (entry.key === draft.key ? next : entry)),
            );
          }}
          onRemove={() => {
            setDrafts((current) => current.filter((entry) => entry.key !== draft.key));
          }}
        />
      ))}

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => {
            setDrafts((current) => [...current, emptyDraft(suggestedReturnLocation ?? "")]);
          }}
          className="flex items-center gap-1.5 text-xs text-primary transition hover:underline"
        >
          <Plus size={14} aria-hidden="true" />
          Add another RMA
        </button>
        <button
          type="submit"
          disabled={complete.length === 0 || duplicateReferences || outcome.isPending}
          className="rounded bg-primary px-3 py-1.5 text-xs text-on-primary transition disabled:opacity-40"
        >
          {outcome.isPending
            ? `Sending... ${formatDuration(elapsed)}`
            : `Send ${String(complete.length)} RMA${complete.length === 1 ? "" : "s"}`}
        </button>
        <button
          type="button"
          // Disabled once the send is in flight, and this is the whole point:
          // it said "Cancel" throughout, and pressing it only closed the form.
          // The POST carried on and `onSuccess` still fired, so an operator who
          // believed they had stopped the send had in fact sent the RMAs. There
          // is no cancel to offer -- aborting the request would not un-write
          // what the server had already committed, and calling that "cancelled"
          // would be the same lie one layer down.
          disabled={outcome.isPending}
          onClick={() => { setOpen(false); }}
          title={outcome.isPending ? "The send has started and cannot be stopped." : undefined}
          className="text-xs text-on-surface-variant transition hover:text-on-surface disabled:cursor-not-allowed disabled:opacity-40"
        >
          Cancel
        </button>
      </div>
      <p className="text-[11px] text-outline">
        The workflow acts on the first response only. Send every RMA for this case together.
      </p>
    </form>
  );
}

function DraftRecordBlock({
  draft,
  index,
  removable,
  unassignedItems,
  claimedElsewhere,
  onChange,
  onRemove,
}: {
  draft: DraftRecord;
  index: number;
  removable: boolean;
  unassignedItems: readonly SelectedItemProjection[];
  claimedElsewhere: ReadonlySet<string>;
  onChange: (next: DraftRecord) => void;
  onRemove: () => void;
}) {
  const position = index + 1;
  return (
    <fieldset className="flex flex-col gap-2 rounded-md border border-outline-variant p-2.5">
      <legend className="flex items-center gap-2 px-1 text-[11px] text-outline">
        RMA {position}
        {removable ? (
          <button
            type="button"
            onClick={onRemove}
            aria-label={`Remove RMA ${String(position)}`}
            className="text-on-surface-variant transition hover:text-error"
          >
            <Trash2 size={12} />
          </button>
        ) : null}
      </legend>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <TextField
          label={`RMA ${String(position)} number`}
          value={draft.returnReference}
          onChange={(value) => { onChange({ ...draft, returnReference: value }); }}
          required
        />
        <TextField
          label={`RMA ${String(position)} tracking`}
          value={draft.trackingReference}
          onChange={(value) => { onChange({ ...draft, trackingReference: value }); }}
        />
        <TextField
          label={`RMA ${String(position)} label`}
          value={draft.labelReference}
          onChange={(value) => { onChange({ ...draft, labelReference: value }); }}
        />
        <TextField
          label={`RMA ${String(position)} return to`}
          value={draft.returnLocation}
          onChange={(value) => { onChange({ ...draft, returnLocation: value }); }}
        />
        <TextField
          label={`RMA ${String(position)} pickup`}
          value={draft.shippingInstructionReference}
          onChange={(value) => { onChange({ ...draft, shippingInstructionReference: value }); }}
          className="sm:col-span-2"
        />
      </div>

      {unassignedItems.length === 0 ? null : (
        <div className="flex flex-col gap-1">
          <span className="text-[11px] text-outline">Lines this RMA covers</span>
          {unassignedItems.map((line) => {
            const claimed = claimedElsewhere.has(line.orderLineReference);
            const checked = draft.orderLineReferences.includes(line.orderLineReference);
            return (
              <label
                key={line.returnItemId}
                className={`flex min-w-0 items-baseline gap-2 text-[11px] ${
                  claimed ? "text-outline" : "text-on-surface"
                }`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  // A line belongs to one RMA. Offering it on a second block
                  // would let Support send two RMAs both claiming it, which the
                  // case model cannot represent.
                  disabled={claimed}
                  onChange={(event) => {
                    onChange({
                      ...draft,
                      orderLineReferences: event.target.checked
                        ? [...draft.orderLineReferences, line.orderLineReference]
                        : draft.orderLineReferences.filter(
                            (reference) => reference !== line.orderLineReference,
                          ),
                    });
                  }}
                />
                <span className="min-w-0 break-all font-mono">{line.orderLineReference}</span>
                <span className="shrink-0 text-outline">x{line.quantity}</span>
                {claimed ? <span className="shrink-0 text-outline">(on another RMA)</span> : null}
              </label>
            );
          })}
        </div>
      )}
    </fieldset>
  );
}

/**
 * One message, attributed.
 *
 * Support knows it is talking to an agent -- that is the stated design, not
 * something to hide -- so who said what is marked plainly rather than styled
 * to look human.
 *
 * **A message with newlines is a document, and is rendered as one.** The
 * opening request is sectioned -- Case, Customer, Order, Return Details, Bay
 * Assignment, Verification -- and HTML collapses runs of whitespace, so without
 * this every section ran together into a single paragraph and each label lost
 * the value it belongs to. A structured handoff nobody can read is not a
 * handoff.
 *
 * Three things follow from that and each earns its place:
 *
 * * `whitespace-pre` rather than `pre-wrap`, so a long value -- an ISO
 *   timestamp, a case id -- stays on the line whose label introduces it instead
 *   of wrapping into the next field's position.
 * * `overflow-x-auto`, because the consequence of not wrapping is that the
 *   widest line decides the width. It scrolls inside its own bubble; the page
 *   does not.
 * * the full column width, because an 80% bubble is right for a sentence and
 *   wrong for a table of fields.
 *
 * A single-line message -- every reply a human types, and every reminder -- is
 * untouched and still renders as an ordinary chat bubble.
 */
function Message({ message }: { message: SupportMessage }) {
  const fromAgent = message.senderRole === "AGENT";
  const isReminder = "reminderKey" in message.businessPayload;
  const isStructured = message.messageText.includes("\n");
  return (
    <li className={`flex gap-3 ${fromAgent ? "" : "justify-end"}`}>
      {fromAgent ? (
        <span
          aria-hidden="true"
          className="mt-1 flex size-6 shrink-0 items-center justify-center rounded bg-surface-container text-on-surface-variant"
        >
          <Bot size={14} />
        </span>
      ) : null}
      <div
        className={`${
          isStructured
            ? "min-w-0 flex-1 overflow-x-auto whitespace-pre font-mono text-[12px]"
            : "max-w-[80%] whitespace-pre-wrap break-words text-sm"
        } rounded-2xl px-3.5 py-2.5 leading-relaxed ${
          fromAgent
            ? "rounded-tl-sm border border-outline-variant bg-surface-container-low text-on-surface"
            : "rounded-tr-sm bg-primary text-on-primary"
        }`}
      >
        {message.messageText}
        {isReminder ? (
          <span className="mt-1.5 block text-[11px] italic text-outline">Follow-up</span>
        ) : null}
      </div>
      {fromAgent ? null : (
        <span
          aria-hidden="true"
          className="mt-1 flex size-6 shrink-0 items-center justify-center rounded bg-surface-container text-on-surface-variant"
        >
          <UserRound size={14} />
        </span>
      )}
    </li>
  );
}

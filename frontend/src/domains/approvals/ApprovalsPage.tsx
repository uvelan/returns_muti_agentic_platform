import { useState } from "react";
import { skipToken, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ClipboardCheck, Inbox } from "lucide-react";

import {
  DECISIONS_BY_STATUS,
  proposalsApi,
  type ProposalDetail,
  type ProposalDiffEntry,
  type ProposalStatus,
  type ProposalSummary,
  type ProposalType,
} from "../../api/proposals";
import { useCapabilities } from "../../hooks/capabilityContext";
import { DomainRail, RailFact, RailNote, RailSection } from "../DomainRail";

/**
 * UI-01 -- the Approvals screen.
 *
 * `ProposalKernel` has recorded every change awaiting a human since it was
 * built, and served that queue to nobody: there was no route in the shell and
 * no directory under `domains/`. A schema draft could be validated, an agent
 * configuration edited and a feedback improvement raised, and the record of
 * "someone has to decide this" existed only in Mongo.
 *
 * **An independent domain, not a tab.** A reviewer's question is "what is
 * waiting on me", which is not a question about configuration, or about the
 * analyzer, or about AI -- it spans all three, which is exactly why the kernel
 * is one inbox. Nesting it under any one of them would have made the other two
 * invisible from it.
 *
 * **The kernel owns the lifecycle; this screen presents it and sends
 * decisions.** There is no transition logic here beyond
 * `DECISIONS_BY_STATUS`, which mirrors the backend's own table so an operator
 * is not offered a button that returns 409 -- and when the kernel refuses
 * anyway, because someone else decided first, the refusal is shown verbatim
 * rather than translated into a guess.
 *
 * **A proposal carries the whole basis for its decision**: before, after, the
 * derived diff, affected keys, risk, evidence references, the validation
 * receipt and the transition history. All of it is on the detail pane, because
 * a reviewer approving a row is approving *this document* and an approval made
 * from a title is not a review.
 */

const STATUS_FILTERS = [
  // Default. "What is waiting on me" is the question the screen exists for, and
  // opening on every proposal ever raised buries it.
  { label: "Waiting on a decision", value: "REVIEW_PENDING" },
  { label: "Approved", value: "APPROVED" },
  { label: "Activated", value: "ACTIVATED" },
  { label: "Rejected", value: "REJECTED" },
  { label: "Draft", value: "DRAFT" },
  { label: "Validated", value: "VALIDATED" },
  { label: "Superseded", value: "SUPERSEDED" },
  { label: "All", value: "" },
] as const;

const TYPE_FILTERS = [
  { label: "All kinds", value: "" },
  { label: "Graph schema", value: "GRAPH_SCHEMA" },
  { label: "Configuration", value: "CONFIGURATION" },
  { label: "Improvement", value: "IMPROVEMENT" },
] as const;

export function ApprovalsPage() {
  const { can } = useCapabilities();
  const client = useQueryClient();
  const [status, setStatus] = useState<string>("REVIEW_PENDING");
  const [type, setType] = useState<string>("");
  const [selected, setSelected] = useState<string | null>(null);

  const canRead = can("governance.proposal.read");

  const queue = useQuery({
    queryKey: ["proposals", status, type],
    queryFn: () =>
      proposalsApi.list({
        status: status === "" ? undefined : (status as ProposalStatus),
        type: type === "" ? undefined : (type as ProposalType),
      }),
    enabled: canRead,
  });

  const detail = useQuery({
    queryKey: ["proposal", selected],
    queryFn: selected === null ? skipToken : () => proposalsApi.get(selected),
  });

  const decide = useMutation({
    mutationFn: ({
      action,
      proposalId,
      note,
    }: {
      action: "APPROVE" | "REJECT" | "ACTIVATE";
      proposalId: string;
      note: string | null;
    }) => {
      if (action === "APPROVE") return proposalsApi.approve(proposalId, note);
      if (action === "REJECT") return proposalsApi.reject(proposalId, note);
      return proposalsApi.activate(proposalId);
    },
    onSuccess: async () => {
      // The queue is filtered by status, and a decision changes the status, so
      // the row the reviewer just acted on belongs somewhere else now.
      await Promise.all([
        client.invalidateQueries({ queryKey: ["proposals"] }),
        client.invalidateQueries({ queryKey: ["proposal", selected] }),
      ]);
    },
  });

  if (!canRead) {
    return (
      <p className="text-sm text-on-surface-variant">
        You do not have access to approvals. Viewing the queue requires
        governance.proposal.read.
      </p>
    );
  }

  return (
    <div className="grid h-[calc(100vh-8rem)] grid-cols-1 gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      {/*
        What the reviewer is currently looking at, and what they may do with it.
        The type breakdown is over the *fetched* queue rather than a second
        count query: one inbox for schema drafts, configuration edits and
        improvements is the kernel's whole design, and the useful question on a
        mixed queue is what the mix is.
      */}
      <DomainRail>
        <RailSection title="Queue">
          {queue.error !== null ? (
            <RailNote>The queue could not be read.</RailNote>
          ) : queue.isPending ? (
            <RailNote>Loading...</RailNote>
          ) : (
            <>
              <RailFact label="Status" value={status === "" ? "Any" : status} />
              <RailFact label="Type" value={type === "" ? "Any" : type} />
              <RailFact label="Waiting" value={queue.data.length} />
            </>
          )}
        </RailSection>
        {queue.data === undefined || queue.data.length === 0 ? null : (
          <RailSection title="By type">
            {[...new Set(queue.data.map((proposal) => proposal.proposalType))]
              .sort()
              .map((proposalType) => (
                <RailFact
                  key={proposalType}
                  label={proposalType}
                  value={
                    queue.data.filter((proposal) => proposal.proposalType === proposalType).length
                  }
                />
              ))}
          </RailSection>
        )}
        <RailSection title="You may">
          {/* Presentation only; the kernel re-checks both server-side. The
              approve/activate split is real -- approving records a decision and
              activating applies it -- so naming them separately matters. */}
          <RailFact label="Approve or reject" value={can("governance.proposal.approve") ? "yes" : "no"} />
          <RailFact label="Activate" value={can("governance.proposal.activate") ? "yes" : "no"} />
        </RailSection>
      </DomainRail>
      <QueuePane
        status={status}
        onStatusChange={(value) => {
          setStatus(value);
          // The selection almost certainly falls outside the new filter, and a
          // detail pane showing a proposal that is not in the list beside it
          // reads as a stuck screen.
          setSelected(null);
        }}
        type={type}
        onTypeChange={(value) => {
          setType(value);
          setSelected(null);
        }}
        proposals={queue.data ?? []}
        loading={queue.isPending}
        error={queue.error}
        selected={selected}
        onSelect={setSelected}
      />
      <DetailPane
        proposal={detail.data ?? null}
        loading={selected !== null && detail.isPending}
        error={detail.error}
        canDecide={can("governance.proposal.approve")}
        canActivate={can("governance.proposal.activate")}
        deciding={decide.isPending}
        decisionError={decide.error}
        onDecide={(action, proposalId, note) => {
          decide.mutate({ action, proposalId, note });
        }}
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

function StatusPill({ status }: { status: ProposalStatus }) {
  const tone =
    status === "REJECTED"
      ? "border-error text-error"
      : status === "REVIEW_PENDING"
        ? "border-primary text-primary"
        : "border-outline-variant text-on-surface-variant";
  return (
    <span className={`rounded-full border px-1.5 py-0.5 text-[10px] uppercase ${tone}`}>
      {status}
    </span>
  );
}

/**
 * Risk is derived from the *shape* of the change -- a removal is HIGH because
 * it is the only class that destroys something a running system may still read.
 * Coloured accordingly, so "why is this HIGH" has an answer on the same row.
 */
function RiskPill({ risk }: { risk: string }) {
  const tone =
    risk === "HIGH"
      ? "border-error text-error"
      : risk === "MEDIUM"
        ? "border-primary text-primary"
        : "border-outline-variant text-on-surface-variant";
  return (
    <span className={`rounded-full border px-1.5 py-0.5 text-[10px] uppercase ${tone}`}>
      {risk}
    </span>
  );
}

function when(value: string | null): string {
  if (value === null) return "-";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function FilterRow({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly { readonly label: string; readonly value: string }[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-[11px] text-outline">
      {label}
      <select
        value={value}
        onChange={(event) => {
          // Matched against the declared options rather than asserted: the
          // element hands back a `string`, and an assertion would let a renamed
          // option through as a status the backend rejects with a 422.
          const chosen = options.find((option) => option.value === event.target.value);
          if (chosen !== undefined) onChange(chosen.value);
        }}
        className="flex-1 rounded border border-outline-variant bg-surface px-2 py-1 text-xs text-on-surface outline-none transition focus:border-primary focus:ring-1 focus:ring-primary"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function QueuePane({
  status,
  onStatusChange,
  type,
  onTypeChange,
  proposals,
  loading,
  error,
  selected,
  onSelect,
}: {
  status: string;
  onStatusChange: (value: string) => void;
  type: string;
  onTypeChange: (value: string) => void;
  proposals: readonly ProposalSummary[];
  loading: boolean;
  error: Error | null;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <Pane title="Proposals">
      <div className="grid grid-cols-1 gap-2 border-b border-outline-variant px-3 py-2 sm:grid-cols-2">
        <FilterRow label="Status" options={STATUS_FILTERS} value={status} onChange={onStatusChange} />
        <FilterRow label="Kind" options={TYPE_FILTERS} value={type} onChange={onTypeChange} />
      </div>

      <div className="flex-1 overflow-y-auto">
        {/*
          Three states, not two. `data ?? []` would tell a reviewer their queue
          is clear when the truth is that we could not ask -- and "nothing is
          waiting on me" is precisely the answer they would act on by leaving.
        */}
        {error !== null ? (
          <p role="alert" className="px-4 py-3 text-sm text-error">
            {error.message}
          </p>
        ) : loading ? (
          <p className="px-4 py-3 text-sm text-on-surface-variant">Loading...</p>
        ) : proposals.length === 0 ? (
          <p className="flex items-center gap-2 px-4 py-3 text-sm text-on-surface-variant">
            <Inbox size={15} aria-hidden="true" />
            Nothing matches this filter.
          </p>
        ) : (
          <ul>
            {proposals.map((proposal) => (
              <li key={proposal.proposalId}>
                <button
                  type="button"
                  onClick={() => {
                    onSelect(proposal.proposalId);
                  }}
                  className={`flex w-full flex-col gap-1 border-b border-outline-variant px-4 py-2.5 text-left transition hover:bg-surface-container ${
                    proposal.proposalId === selected ? "bg-surface-container" : ""
                  }`}
                >
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-sm text-on-surface">{proposal.title}</span>
                    <StatusPill status={proposal.status} />
                    <RiskPill risk={proposal.risk} />
                  </span>
                  <span className="flex flex-wrap items-center gap-x-3 text-[11px] text-outline">
                    <span>{proposal.proposalType}</span>
                    <span>{proposal.subjectId}</span>
                    <span>{proposal.proposedBy}</span>
                    <span>{when(proposal.updatedAt)}</span>
                    {/*
                      How much of the platform this touches. A reviewer sorting
                      a queue by "what could hurt" needs the size of the blast
                      radius, not only the risk class.
                    */}
                    <span>{proposal.affectedKeys.length} keys</span>
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

function Facts({ rows }: { rows: readonly (readonly [string, string])[] }) {
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-xs">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-outline">{label}</dt>
          <dd className="break-words text-on-surface">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A diff leaf's value. `undefined` is "this side does not have the key". */
function renderValue(value: unknown): string {
  if (value === undefined) return "-";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function DiffTable({ diff }: { diff: readonly ProposalDiffEntry[] }) {
  if (diff.length === 0) {
    return (
      <p className="text-xs text-on-surface-variant">
        {/* Derived from before/after by the kernel, so an empty diff means the
            documents agree -- not that the diff failed to load. */}
        The before and after documents are identical.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead className="text-[10px] uppercase tracking-wide text-outline">
          <tr>
            <th className="py-1 pr-3">Key</th>
            <th className="py-1 pr-3">Change</th>
            <th className="py-1 pr-3">Before</th>
            <th className="py-1">After</th>
          </tr>
        </thead>
        <tbody>
          {diff.map((entry) => (
            <tr key={entry.key} className="border-t border-outline-variant align-top">
              <td className="break-all py-1 pr-3 font-mono text-on-surface">{entry.key}</td>
              <td
                className={`py-1 pr-3 ${entry.change === "REMOVED" ? "text-error" : "text-on-surface-variant"}`}
              >
                {entry.change}
              </td>
              <td className="break-all py-1 pr-3 text-on-surface-variant">
                {renderValue(entry.before)}
              </td>
              <td className="break-all py-1 text-on-surface">{renderValue(entry.after)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DecisionControls({
  proposal,
  canDecide,
  canActivate,
  deciding,
  error,
  onDecide,
}: {
  proposal: ProposalDetail;
  canDecide: boolean;
  canActivate: boolean;
  deciding: boolean;
  error: Error | null;
  onDecide: (
    action: "APPROVE" | "REJECT" | "ACTIVATE",
    proposalId: string,
    note: string | null,
  ) => void;
}) {
  const [note, setNote] = useState("");
  const available = DECISIONS_BY_STATUS[proposal.status] ?? [];

  if (available.length === 0) {
    return (
      <p className="text-xs text-on-surface-variant">
        {/* REJECTED and SUPERSEDED are terminal in the kernel's table, and a
            DRAFT or VALIDATED proposal has not been submitted for review yet --
            it is decided from the surface that raised it. */}
        No decision is available from {proposal.status}.
      </p>
    );
  }

  const wantsDecision = available.includes("APPROVE");
  const wantsActivation = available.includes("ACTIVATE");

  if (wantsDecision && !canDecide) {
    return (
      <p className="text-xs text-on-surface-variant">
        Deciding on a proposal requires governance.proposal.approve.
      </p>
    );
  }
  if (wantsActivation && !canActivate) {
    return (
      <p className="text-xs text-on-surface-variant">
        {/* Separately granted on purpose: approving says the change is right,
            activating says now is the moment -- and here that publishes a
            configuration release. */}
        Activating an approved proposal requires governance.proposal.activate.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {wantsDecision ? (
        <label className="flex flex-col gap-1 text-[11px] text-outline">
          Reason
          <textarea
            value={note}
            rows={2}
            maxLength={1000}
            onChange={(event) => {
              setNote(event.target.value);
            }}
            className="rounded border border-outline-variant bg-surface px-2 py-1.5 text-sm text-on-surface outline-none transition focus:border-primary focus:ring-1 focus:ring-primary"
          />
          <span className="text-outline">
            {/* Optional, because `DecisionRequest.note` is. Requiring one here
                would be a rule the kernel does not have, enforced in the one
                place that cannot enforce anything. */}
            Optional, up to 1000 characters. Recorded on the proposal's history against your name.
          </span>
        </label>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        {available.map((action) => (
          <button
            key={action}
            type="button"
            disabled={deciding}
            onClick={() => {
              onDecide(action, proposal.proposalId, note.trim() === "" ? null : note.trim());
            }}
            className={`rounded px-3 py-1.5 text-xs transition disabled:opacity-40 ${
              action === "REJECT"
                ? "border border-error text-error"
                : "bg-primary text-on-primary"
            }`}
          >
            {action === "APPROVE" ? "Approve" : action === "REJECT" ? "Reject" : "Activate"}
          </button>
        ))}
      </div>

      {error !== null ? (
        // Verbatim. The kernel refuses for genuinely different reasons -- a
        // forbidden key, a stale version because someone else decided first, an
        // activator that is not registered in this process -- and each needs a
        // different response from the reviewer.
        <p role="alert" className="text-xs text-error">
          {error.message}
        </p>
      ) : null}
    </div>
  );
}

function DetailPane({
  proposal,
  loading,
  error,
  canDecide,
  canActivate,
  deciding,
  decisionError,
  onDecide,
}: {
  proposal: ProposalDetail | null;
  loading: boolean;
  error: Error | null;
  canDecide: boolean;
  canActivate: boolean;
  deciding: boolean;
  decisionError: Error | null;
  onDecide: (
    action: "APPROVE" | "REJECT" | "ACTIVATE",
    proposalId: string,
    note: string | null,
  ) => void;
}) {
  if (error !== null) {
    return (
      <Pane title="Proposal">
        <p role="alert" className="px-4 py-3 text-sm text-error">
          {error.message}
        </p>
      </Pane>
    );
  }

  if (loading) {
    return (
      <Pane title="Proposal">
        <p className="px-4 py-3 text-sm text-on-surface-variant">Loading...</p>
      </Pane>
    );
  }

  if (proposal === null) {
    return (
      <Pane title="Proposal">
        <div className="flex flex-1 items-center justify-center p-6 text-center">
          <p className="flex max-w-xs flex-col items-center gap-2 text-sm text-on-surface-variant">
            <ClipboardCheck size={20} aria-hidden="true" />
            Pick a proposal to see what it changes and decide on it.
          </p>
        </div>
      </Pane>
    );
  }

  return (
    <Pane title={proposal.title}>
      <div className="flex flex-1 flex-col gap-5 overflow-y-auto p-4">
        <section className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill status={proposal.status} />
            <RiskPill risk={proposal.risk} />
            <span className="text-[11px] text-outline">{proposal.proposalId}</span>
          </div>
          <Facts
            rows={[
              ["Kind", proposal.proposalType],
              ["Subject", proposal.subjectId],
              ["Proposed by", proposal.proposedBy],
              ["Raised", when(proposal.createdAt)],
              ["Last change", when(proposal.updatedAt)],
              ["Decided by", proposal.decidedBy ?? "-"],
              ...(proposal.decisionNote === null
                ? []
                : ([["Reason", proposal.decisionNote]] as const)),
              ...(proposal.activationReference === null
                ? []
                : ([["Activated into", proposal.activationReference]] as const)),
            ]}
          />
        </section>

        <section className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-outline">Checked</h3>
          {/*
            A VALIDATED proposal with nothing to point at is indistinguishable
            from one nobody checked, so the absence is stated rather than left
            as a blank row.
          */}
          {proposal.validationReceipt === null ? (
            <p className="text-xs text-error">
              No validation receipt. Nothing has certified that this change is sound.
            </p>
          ) : (
            <Facts rows={[["Validation receipt", proposal.validationReceipt]]} />
          )}
        </section>

        <section className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-outline">Evidence</h3>
          {/*
            References, never payloads -- a session id, a validation result id, a
            sync run id. The digest content-addresses what the proposal was built
            from, so "this is the proposal I reviewed" stays checkable afterwards.
          */}
          {proposal.evidence.length === 0 ? (
            <p className="text-xs text-on-surface-variant">No evidence was recorded.</p>
          ) : (
            <ul className="flex flex-col gap-0.5 font-mono text-xs text-on-surface">
              {proposal.evidence.map((reference) => (
                <li key={reference}>{reference}</li>
              ))}
            </ul>
          )}
          <Facts rows={[["Digest", proposal.evidenceDigest]]} />
        </section>

        <section className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-outline">
            Affected keys
          </h3>
          {proposal.affectedKeys.length === 0 ? (
            <p className="text-xs text-on-surface-variant">This change touches no keys.</p>
          ) : (
            <ul className="flex flex-wrap gap-1 font-mono text-[11px] text-on-surface">
              {proposal.affectedKeys.map((key) => (
                <li key={key} className="rounded border border-outline-variant px-1.5 py-0.5">
                  {key}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-outline">
            Before and after
          </h3>
          <DiffTable diff={proposal.diff} />
        </section>

        <section className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-outline">History</h3>
          {proposal.history.length === 0 ? (
            <p className="text-xs text-on-surface-variant">No transitions recorded yet.</p>
          ) : (
            <ol className="flex flex-col gap-1 text-xs">
              {proposal.history.map((entry, index) => (
                <li
                  key={`${entry.status}-${entry.occurred_at}-${String(index)}`}
                  className="flex flex-wrap items-baseline gap-x-2 text-on-surface"
                >
                  <span className="font-medium">{entry.status}</span>
                  <span className="text-outline">{entry.actor}</span>
                  <span className="text-outline">{when(entry.occurred_at)}</span>
                  {entry.note === null ? null : (
                    <span className="text-on-surface-variant">{entry.note}</span>
                  )}
                </li>
              ))}
            </ol>
          )}
        </section>

        <section className="flex flex-col gap-2 border-t border-outline-variant pt-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-outline">Decision</h3>
          <DecisionControls
            proposal={proposal}
            canDecide={canDecide}
            canActivate={canActivate}
            deciding={deciding}
            error={decisionError}
            onDecide={onDecide}
          />
        </section>
      </div>
    </Pane>
  );
}

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  graphSchemaApi,
  type AnalysisSessionView,
  type ProposedChangeView,
  type ReanalysisProposalView,
  type ValidationFindingView,
} from "../../api/graphSchema";
import { schemaReleasesApi, type MigrationPlan } from "../../api/schemaReleases";
import { useCapabilities } from "../../hooks/capabilityContext";
import { SourceBindingsPanel } from "../data-sources/SourceBindingsPanel";

/**
 * The Graph Schema Analyzer screen (Phase 20).
 *
 * **Sources | Graph Canvas | Analyzer Copilot, and the canvas now draws.** It
 * was absent for a real reason: `/api/graph-schema` served `entity_count` and
 * `relationship_count` and nothing else, so a consumer could learn a draft had
 * seven entities and never learn what they were. Rendering a plausible-looking
 * graph from two integers would have been fabrication, so the column stated the
 * gap. `GET /drafts/{id}/shape` closed it.
 *
 * **The canvas is a grid and an edge list, not a force-directed layout.** What
 * a reviewer needs from a schema proposal is to read the entities, their
 * properties and types, and which relationships connect what -- with
 * cardinality, which is the half a count hides completely. A layout engine
 * would add a dependency and a lot of pixels without answering any of that
 * better. A spatial view, if wanted, is additive.
 *
 * The shape is fetched only when a draft is on screen, mirroring why the
 * backend kept it off the counts endpoint: a real source's schema is unbounded.
 *
 * **Never offer a source-side schema modification.** Nothing in this screen
 * writes to a source; the Sources column is strictly read-only, which is also
 * all the API would permit.
 */

const TABS = [
  "Validation",
  "Drift",
  "Releases",
  "Versions",
  "Properties",
  "Mapping",
  "Sources",
  "Indexes",
  "Sync",
] as const;
type Tab = (typeof TABS)[number];

/**
 * The canvas and the Properties/Mapping/Indexes tabs read one payload through
 * one query key. Without a stale time they still *share* the cache entry, but
 * TanStack refetches whenever a second observer mounts, so switching to a tab
 * re-fetched a schema that had not changed. A draft's shape only changes when a
 * mutation changes it, and mutations invalidate the whole `graph-schema` key,
 * so holding it briefly costs nothing and cannot serve a stale shape after an
 * edit.
 */
const SHAPE_STALE_TIME_MS = 30_000;

/**
 * Tabs the analyzer API has no data for. Named, not silently omitted.
 *
 * Only Sync is left, and it is narrower than it was: activating a *schema
 * release* is now the Releases tab, because that is a decision about which
 * schema the platform reasons over. What remains outside this surface is the
 * *generation* lifecycle -- build, fence, drain, retire -- which the analyzer
 * delegates and never performs.
 */
const UNBACKED_TABS: Partial<Record<Tab, string>> = {
  Sync: "Generation build, drain and retirement are lifecycle operations outside this surface. "
    + "Activating a schema release is on the Releases tab.",
};

export function GraphSchemaPage() {
  const { can } = useCapabilities();
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("Validation");

  const analyses = useQuery({
    queryKey: ["graph-schema", "analyses"],
    queryFn: graphSchemaApi.listAnalyses,
  });

  const selected = analyses.data?.find((a) => a.analysis_id === selectedId) ?? null;

  return (
    <div className="flex flex-col gap-4">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Graph Schema Analyzer</h1>
        <p className="mt-1 text-sm text-slate-600">
          Source-driven schema proposal, validation, and approval.
        </p>
      </header>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[18rem_1fr_22rem]">
        <SourcesColumn
          analyses={analyses.data ?? []}
          isLoading={analyses.isLoading}
          error={analyses.error}
          selectedId={selectedId}
          onSelect={setSelectedId}
        />
        <CanvasColumn analysis={selected} />
        <CopilotColumn analysis={selected} canWrite={can("graph_schema.draft.write")} />
      </div>

      <section className="rounded-lg border border-slate-200 bg-white">
        <div role="tablist" aria-label="Draft detail" className="flex gap-1 border-b border-slate-200 px-2">
          {TABS.map((name) => (
            <button
              key={name}
              role="tab"
              type="button"
              aria-selected={tab === name}
              onClick={() => { setTab(name); }}
              className={[
                "px-3 py-2 text-sm font-medium transition",
                tab === name
                  ? "border-b-2 border-slate-900 text-slate-900"
                  : "text-slate-500 hover:text-slate-800",
              ].join(" ")}
            >
              {name}
            </button>
          ))}
        </div>
        <div className="p-4">
          <DetailTab
            tab={tab}
            draftId={selected?.draft_id ?? null}
            canApprove={can("graph_schema.draft.write")}
            canActivate={can("graph_schema.generation.activate")}
            onChanged={() => {
              void queryClient.invalidateQueries({ queryKey: ["graph-schema"] });
            }}
          />
        </div>
      </section>
    </div>
  );
}

function SourcesColumn({
  analyses,
  isLoading,
  error,
  selectedId,
  onSelect,
}: {
  analyses: readonly AnalysisSessionView[];
  isLoading: boolean;
  error: Error | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <aside className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-slate-900">Analyses</h2>
      {isLoading ? <p className="mt-3 text-sm text-slate-500">Loading...</p> : null}
      {error ? (
        <p className="mt-3 text-sm text-red-700">Could not load analyses: {error.message}</p>
      ) : null}
      {!isLoading && !error && analyses.length === 0 ? (
        <p className="mt-3 text-sm text-slate-600">No analyses yet.</p>
      ) : null}
      <ul className="mt-3 flex flex-col gap-1">
        {analyses.map((analysis) => (
          <li key={analysis.analysis_id}>
            <button
              type="button"
              onClick={() => { onSelect(analysis.analysis_id); }}
              aria-current={selectedId === analysis.analysis_id ? "true" : undefined}
              className={[
                "w-full rounded-md px-3 py-2 text-left text-sm transition",
                selectedId === analysis.analysis_id
                  ? "bg-slate-900 text-white"
                  : "text-slate-700 hover:bg-slate-100",
              ].join(" ")}
            >
              <span className="block truncate font-medium">{analysis.analysis_id}</span>
              <span className="block truncate text-xs opacity-80">{analysis.status}</span>
            </button>
          </li>
        ))}
      </ul>
      <p className="mt-4 text-xs text-slate-500">
        Sources are read-only here. The analyzer never offers a source-side schema change.
      </p>
    </aside>
  );
}

function CanvasColumn({ analysis }: { analysis: AnalysisSessionView | null }) {
  const draftId = analysis?.draft_id ?? null;
  const draft = useQuery({
    queryKey: ["graph-schema", "draft", draftId],
    // `enabled` guarantees a non-null id at call time, but the type system
    // cannot see that, so the id is narrowed here rather than asserted.
    queryFn: () => graphSchemaApi.getDraft(draftId ?? ""),
    enabled: draftId !== null,
  });

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-slate-900">Graph</h2>
      {analysis === null ? (
        <p className="mt-3 text-sm text-slate-600">Select an analysis.</p>
      ) : draft.data ? (
        <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt className="text-slate-500">Entities</dt>
            <dd className="text-lg font-semibold text-slate-900">{draft.data.entity_count}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Relationships</dt>
            <dd className="text-lg font-semibold text-slate-900">
              {draft.data.relationship_count}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">Revision</dt>
            <dd className="text-slate-900">{draft.data.current_revision}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Status</dt>
            <dd className="text-slate-900">{draft.data.status}</dd>
          </div>
        </dl>
      ) : (
        <p className="mt-3 text-sm text-slate-600">This analysis has no draft yet.</p>
      )}

      {draftId !== null ? <ShapeCanvas draftId={draftId} /> : null}
    </section>
  );
}

/**
 * The node-and-edge view, drawn from `GET /drafts/{id}/shape`.
 *
 * **Not a force-directed graph library.** A dependency that lays out arbitrary
 * graphs would need bundling, and what an operator reviewing a schema proposal
 * actually needs is to read the entities, their properties, and which
 * relationships connect what -- all of which a grid and an edge list convey
 * exactly, at any size, with no layout to fight. If a spatial view is wanted
 * later it is additive.
 *
 * **Fetched only when a draft is selected**, matching why the backend kept the
 * shape off the counts endpoint: a real source's schema is unbounded.
 */
function ShapeCanvas({ draftId }: { draftId: string }) {
  const shape = useQuery({
    queryKey: ["graph-schema", "shape", draftId],
    queryFn: () => graphSchemaApi.getDraftShape(draftId),
    staleTime: SHAPE_STALE_TIME_MS,
  });

  if (shape.isLoading) {
    return <p className="mt-4 border-t border-slate-200 pt-4 text-sm text-slate-500">Loading shape...</p>;
  }
  if (shape.error) {
    return (
      <p className="mt-4 border-t border-slate-200 pt-4 text-sm text-red-700">
        {shape.error.message}
      </p>
    );
  }

  const entities = Object.entries(shape.data?.entities ?? {});
  const relationships = shape.data?.relationships ?? [];

  if (entities.length === 0 && relationships.length === 0) {
    // An empty shape and a missing draft are different answers; the backend
    // returns the former rather than 404 precisely so this can say which.
    return (
      <p className="mt-4 border-t border-slate-200 pt-4 text-sm text-slate-600">
        This draft has no entities yet.
      </p>
    );
  }

  return (
    <div className="mt-4 border-t border-slate-200 pt-4">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Entities</h3>
      <ul className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
        {entities.map(([label, entity]) => (
          <li key={label} className="rounded-md border border-slate-200 p-2">
            <p className="text-sm font-semibold text-slate-900">{label}</p>
            <p className="text-xs text-slate-500">
              from {entity.source_dataset ?? "unmapped"}
              {entity.sync_mode !== null ? ` - ${entity.sync_mode}` : ""}
            </p>
            <ul className="mt-1 flex flex-col gap-0.5">
              {Object.entries(entity.properties).map(([name, property]) => (
                <li key={name} className="flex justify-between gap-2 text-xs">
                  <span
                    className={
                      // Identifiers carry the entity's identity: which
                      // properties they are is the first thing a reviewer
                      // checks, so they are marked rather than buried.
                      entity.identifier_properties.includes(name)
                        ? "font-semibold text-slate-900"
                        : "text-slate-700"
                    }
                  >
                    {name}
                    {entity.identifier_properties.includes(name) ? " (id)" : ""}
                  </span>
                  <span className="text-slate-500">{property.type}</span>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>

      <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-slate-500">
        Relationships
      </h3>
      {relationships.length === 0 ? (
        <p className="mt-1 text-sm text-slate-600">No relationships defined.</p>
      ) : (
        <ul className="mt-2 flex flex-col gap-1">
          {relationships.map((edge) => (
            <li
              key={`${edge.from_label}-${edge.relationship_type}-${edge.to_label}`}
              className="text-sm text-slate-800"
            >
              <span className="font-medium">{edge.from_label}</span>
              {" -["}
              <span className="font-mono text-xs">{edge.relationship_type}</span>
              {"]-> "}
              <span className="font-medium">{edge.to_label}</span>
              {/* Cardinality is half of what an edge means: two drafts with the
                  same relationship count can describe entirely different graphs. */}
              {edge.cardinality !== null ? (
                <span className="ml-2 text-xs text-slate-500">{edge.cardinality}</span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CopilotColumn({
  analysis,
  canWrite,
}: {
  analysis: AnalysisSessionView | null;
  canWrite: boolean;
}) {
  const queryClient = useQueryClient();
  const [answers, setAnswers] = useState<Record<string, string>>({});

  const analysisId = analysis?.analysis_id ?? null;

  const clarifications = useQuery({
    queryKey: ["graph-schema", "clarifications", analysisId],
    queryFn: () => graphSchemaApi.listClarifications(analysisId ?? ""),
    enabled: analysisId !== null,
  });

  const answer = useMutation({
    mutationFn: ({ id, text }: { id: string; text: string }) =>
      graphSchemaApi.answerClarification(analysisId ?? "", id, text),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["graph-schema"] });
    },
  });

  const open = (clarifications.data ?? []).filter((c) => c.answer === null);
  const answered = (clarifications.data ?? []).filter((c) => c.answer !== null);

  return (
    <aside className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-slate-900">Analyzer Copilot</h2>

      {analysis === null ? (
        <p className="mt-3 text-sm text-slate-600">Select an analysis.</p>
      ) : (
        <>
          {open.length === 0 ? (
            <p className="mt-3 text-sm text-slate-600">No open clarifications.</p>
          ) : null}

          {open.map((clarification) => (
            <div key={clarification.clarification_id} className="mt-4">
              <p className="text-sm text-slate-800">{clarification.question}</p>
              <textarea
                aria-label={`Answer: ${clarification.question}`}
                value={answers[clarification.clarification_id] ?? ""}
                onChange={(event) => {
                  setAnswers((prior) => ({
                    ...prior,
                    [clarification.clarification_id]: event.target.value,
                  }));
                }}
                disabled={!canWrite}
                rows={3}
                className="mt-2 w-full rounded-md border border-slate-300 p-2 text-sm disabled:bg-slate-100"
              />
              <button
                type="button"
                disabled={
                  !canWrite
                  || answer.isPending
                  || !(answers[clarification.clarification_id] ?? "").trim()
                }
                onClick={() => {
                  answer.mutate({
                    id: clarification.clarification_id,
                    text: answers[clarification.clarification_id] ?? "",
                  });
                }}
                className="mt-2 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300"
              >
                Submit answer
              </button>
            </div>
          ))}

          {answer.error ? (
            <p className="mt-3 text-sm text-red-700">{answer.error.message}</p>
          ) : null}

          {!canWrite ? (
            <p className="mt-3 text-xs text-slate-500">
              You have read access only; answering requires graph_schema.draft.write.
            </p>
          ) : null}

          {answered.length > 0 ? (
            <div className="mt-6 border-t border-slate-200 pt-3">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Answered
              </h3>
              <ul className="mt-2 flex flex-col gap-2">
                {answered.map((clarification) => (
                  <li key={clarification.clarification_id} className="text-xs text-slate-600">
                    <p className="text-slate-800">{clarification.question}</p>
                    <p className="mt-0.5">{clarification.answer}</p>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </>
      )}
    </aside>
  );
}

function DetailTab({
  tab,
  draftId,
  canApprove,
  canActivate,
  onChanged,
}: {
  tab: Tab;
  draftId: string | null;
  canApprove: boolean;
  canActivate: boolean;
  onChanged: () => void;
}) {
  const unbacked = UNBACKED_TABS[tab];
  if (unbacked) {
    return <p className="text-sm text-slate-500">{unbacked}</p>;
  }
  // Releases are the platform's, not one analysis's: which schema is live is a
  // fact about the runtime, and it has to be readable before anyone has picked
  // a draft to look at.
  if (tab === "Releases") {
    return <ReleasesTab canActivate={canActivate} />;
  }
  if (draftId === null) {
    return <p className="text-sm text-slate-600">Select an analysis with a draft.</p>;
  }
  switch (tab) {
    case "Validation":
      return <ValidationTab draftId={draftId} canApprove={canApprove} onChanged={onChanged} />;
    case "Drift":
      return <DriftTab draftId={draftId} canApply={canApprove} onChanged={onChanged} />;
    case "Sources":
      return <SourceBindingsPanel canRebind={canApprove} />;
    case "Versions":
      return <VersionsTab draftId={draftId} />;
    default:
      return <ShapeTab draftId={draftId} tab={tab} />;
  }
}

/**
 * Re-run discovery, and read what the source did while nobody was looking.
 *
 * **Every proposal is a button, never an effect.** Running a re-analysis
 * changes no draft; each proposed change is applied only when someone clicks
 * it, and the click goes through the ordinary mutations endpoint so the
 * revision history records it the same way it records a hand-written edit.
 *
 * **Accepting one change does not commit to the rest.** Each group is
 * self-contained -- an entity with its properties, or one property's
 * remove-then-add retype -- so the batches are independently applicable and the
 * screen can offer them one at a time honestly.
 *
 * A change with no commands is shown as a question rather than hidden. It is
 * where the analyzer declined to guess, which is exactly the part a human is
 * needed for.
 */
function DriftTab({
  draftId,
  canApply,
  onChanged,
}: {
  draftId: string;
  canApply: boolean;
  onChanged: () => void;
}) {
  const [proposal, setProposal] = useState<ReanalysisProposalView | null>(null);
  const [accepted, setAccepted] = useState<readonly string[]>([]);

  const reanalyze = useMutation({
    mutationFn: () => graphSchemaApi.reanalyzeDraft(draftId),
    onSuccess: (data) => {
      setProposal(data);
      setAccepted([]);
      // The analysis re-grounded on a new snapshot, so anything showing the
      // old one is stale even though no draft changed.
      onChanged();
    },
  });

  const apply = useMutation({
    mutationFn: ({ change }: { change: ProposedChangeView; key: string }) =>
      graphSchemaApi.applyMutations(draftId, change.mutations),
    onSuccess: (_data, variables) => {
      setAccepted((prior) => [...prior, variables.key]);
      onChanged();
    },
  });

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => { reanalyze.mutate(); }}
          disabled={reanalyze.isPending}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-800 disabled:opacity-50"
        >
          Re-analyse sources
        </button>
        <p className="text-xs text-slate-500">
          Reads metadata only, proposes changes, and applies none of them.
        </p>
      </div>

      {reanalyze.error ? (
        <p className="mt-3 text-sm text-red-700">{reanalyze.error.message}</p>
      ) : null}
      {apply.error ? <p className="mt-3 text-sm text-red-700">{apply.error.message}</p> : null}

      {proposal === null ? null : proposal.changes.length === 0
        && proposal.rebindings.length === 0 ? (
          <p className="mt-4 text-sm text-slate-700">
            The sources look the same as when this draft was designed
            {/* Two captures of the same shape share a content address, which is
                the actual reason there is nothing to show. */}
            <span className="block text-xs text-slate-500">
              content hash {proposal.to_content_hash.slice(0, 12)} is unchanged.
            </span>
          </p>
        ) : (
          <div className="mt-4 flex flex-col gap-4">
            {proposal.rebindings.length > 0 ? (
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Moved, not changed
                </h3>
                <ul className="mt-2 flex flex-col gap-2">
                  {proposal.rebindings.map((rebinding) => (
                    <li
                      key={rebinding.dataset}
                      className="rounded-md border border-sky-200 bg-sky-50 p-3 text-sm"
                    >
                      <p className="font-medium text-slate-900">
                        {rebinding.dataset}
                        {" -> "}
                        {rebinding.to_dataset}
                        <span className="ml-2 font-normal text-slate-600">
                          ({rebinding.from_source_id} to {rebinding.to_source_id})
                        </span>
                      </p>
                      <p className="mt-1 text-slate-700">{rebinding.detail}</p>
                      <p className="mt-1 text-xs text-slate-500">
                        Fix this on the Sources tab. Nothing in the graph&apos;s shape changes.
                      </p>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            {proposal.changes.length > 0 ? (
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Proposed changes
                </h3>
                <ul className="mt-2 flex flex-col gap-2">
                  {proposal.changes.map((change, index) => {
                    const key = `${change.drift}-${change.element}-${String(index)}`;
                    const isAccepted = accepted.includes(key);
                    return (
                      <li key={key} className="rounded-md border border-slate-200 p-3 text-sm">
                        <p className="flex flex-wrap items-baseline gap-2">
                          <span className="font-mono text-xs text-slate-500">{change.drift}</span>
                          <span className="font-medium text-slate-900">{change.element}</span>
                          <span className="text-xs text-slate-500">in {change.dataset}</span>
                        </p>
                        <p className="mt-1 text-slate-700">{change.detail}</p>
                        {change.mutations.length === 0 ? (
                          <p className="mt-2 text-xs font-medium text-amber-800">
                            Needs your decision -- no command can express this without guessing.
                          </p>
                        ) : (
                          <div className="mt-2 flex flex-wrap items-center gap-2">
                            <span className="font-mono text-xs text-slate-500">
                              {change.mutations.map((command) => command.kind).join(", ")}
                            </span>
                            <button
                              type="button"
                              disabled={!canApply || isAccepted || apply.isPending}
                              onClick={() => { apply.mutate({ change, key }); }}
                              className="rounded-md bg-slate-900 px-3 py-1 text-xs font-medium text-white disabled:bg-slate-300"
                            >
                              {isAccepted ? "Applied" : "Accept"}
                            </button>
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </section>
            ) : null}

            {proposal.diff.entries.length > 0 ? (
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  If every proposal is accepted
                </h3>
                <ul className="mt-2 flex flex-col gap-1 text-sm">
                  {proposal.diff.entries.map((entry) => (
                    <li key={`${entry.change_type}-${entry.element}`}>
                      <span className="font-mono text-xs text-slate-500">{entry.change_type}</span>{" "}
                      <span className="font-medium text-slate-900">{entry.element}</span>
                      <span className="text-slate-600"> -- {entry.detail}</span>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
          </div>
        )}

      {!canApply ? (
        <p className="mt-3 text-xs text-slate-500">
          You have read access only; accepting a change requires graph_schema.draft.write.
        </p>
      ) : null}
    </div>
  );
}

/**
 * Which schema the platform runs, and what moving to another one costs.
 *
 * **The plan comes before the button.** Selecting a release fetches a preview
 * that writes nothing, so an operator can read what activation would do --
 * added and removed labels, the constraints that would be created or dropped,
 * and whether the graph can absorb it incrementally -- without half-performing
 * it. Activation then records that same plan and returns it.
 *
 * A FULL_REBUILD is stated with its reasons, because a rebuild verdict without
 * a why is not something anyone can act on.
 */
function ReleasesTab({ canActivate }: { canActivate: boolean }) {
  const client = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<MigrationPlan | null>(null);

  const releases = useQuery({
    queryKey: ["schema-releases"],
    queryFn: () => schemaReleasesApi.list(),
  });

  const plan = useQuery({
    queryKey: ["schema-releases", "plan", selected],
    queryFn: () => schemaReleasesApi.migrationPlan(selected ?? ""),
    enabled: selected !== null,
  });

  const activate = useMutation({
    mutationFn: (releaseId: string) => schemaReleasesApi.activate(releaseId),
    onSuccess: async (data) => {
      setOutcome(data);
      await client.invalidateQueries({ queryKey: ["schema-releases"] });
    },
  });

  if (releases.error) return <p className="text-sm text-red-700">{releases.error.message}</p>;
  if (releases.isPending) return <p className="text-sm text-slate-600">Loading...</p>;
  if (releases.data.releases.length === 0) {
    return (
      <p className="text-sm text-slate-600">
        Nothing has been published yet, so the platform is running the schema file shipped with
        it. Publishing an approved draft cuts the first release.
      </p>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[20rem_1fr]">
      <ul className="flex flex-col gap-1">
        {releases.data.releases.map((release) => (
          <li key={release.configurationReleaseId}>
            <button
              type="button"
              onClick={() => {
                setSelected(release.configurationReleaseId);
                setOutcome(null);
              }}
              aria-current={selected === release.configurationReleaseId ? "true" : undefined}
              className={[
                "w-full rounded-md px-3 py-2 text-left text-sm transition",
                selected === release.configurationReleaseId
                  ? "bg-slate-900 text-white"
                  : "text-slate-700 hover:bg-slate-100",
              ].join(" ")}
            >
              <span className="block truncate font-medium">
                {release.configurationReleaseId}
              </span>
              <span className="block truncate text-xs opacity-80">
                {release.active ? "live" : "published"}
                {release.publishedBy !== null ? ` - ${release.publishedBy}` : ""}
              </span>
            </button>
          </li>
        ))}
      </ul>

      <div>
        {selected === null ? (
          <p className="text-sm text-slate-600">
            Select a release to see what activating it would do to the graph.
          </p>
        ) : plan.isPending ? (
          <p className="text-sm text-slate-600">Planning...</p>
        ) : plan.error ? (
          <p className="text-sm text-red-700">{plan.error.message}</p>
        ) : (
          <MigrationPlanPanel
            // The plan activation actually recorded wins over the preview: they
            // agree today, and if the active pointer moved under us they would
            // not, so showing the returned one is showing what happened.
            plan={outcome ?? plan.data}
            activated={outcome !== null}
            isActive={releases.data.activeReleaseId === selected}
            canActivate={canActivate}
            isPending={activate.isPending}
            error={activate.error}
            onActivate={() => { activate.mutate(selected); }}
          />
        )}
      </div>
    </div>
  );
}

// Tone tracks cost to the operator, not severity: the two cheap tiers read as
// routine, a full rebuild reads as something to plan for. BACKFILL and
// AFFECTED_SCOPE_RESYNC are GRAPH-02's additions -- before them every mapping
// change was priced as a rebuild. INCREMENTAL is no longer produced but is kept
// here because a plan recorded before those classes existed still renders.
const STRATEGY_TONE: Record<MigrationPlan["strategy"], string> = {
  NO_CHANGE: "bg-slate-100 text-slate-800",
  INCREMENTAL: "bg-emerald-100 text-emerald-900",
  BACKFILL: "bg-emerald-100 text-emerald-900",
  AFFECTED_SCOPE_RESYNC: "bg-sky-100 text-sky-900",
  FULL_REBUILD: "bg-amber-100 text-amber-900",
};

function MigrationPlanPanel({
  plan,
  activated,
  isActive,
  canActivate,
  isPending,
  error,
  onActivate,
}: {
  plan: MigrationPlan;
  activated: boolean;
  isActive: boolean;
  canActivate: boolean;
  isPending: boolean;
  error: Error | null;
  onActivate: () => void;
}) {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <span
          className={`rounded-full px-3 py-1 text-xs font-semibold ${STRATEGY_TONE[plan.strategy]}`}
        >
          {plan.strategy}
        </span>
        <span className="text-sm text-slate-600">
          {plan.from_release_id ?? "nothing active"} to {plan.to_release_id}
        </span>
        <button
          type="button"
          onClick={onActivate}
          disabled={!canActivate || isActive || isPending}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300"
        >
          {isActive ? "Live" : "Activate"}
        </button>
      </div>

      {error ? <p className="text-sm text-red-700">{error.message}</p> : null}
      {activated ? (
        <p className="text-sm font-medium text-slate-900">
          Activated. This plan is recorded against the release.
        </p>
      ) : null}
      {!canActivate ? (
        <p className="text-xs text-slate-500">
          You can read this plan; activating requires graph_schema.generation.activate.
        </p>
      ) : null}

      {plan.rebuild_reasons.length > 0 ? (
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Why a full rebuild
          </h3>
          <ul className="mt-2 list-disc pl-5 text-sm text-slate-700">
            {plan.rebuild_reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </section>
      ) : null}

      <ElementList title="Node labels added" items={plan.node_labels_added} />
      <ElementList title="Node labels removed" items={plan.node_labels_removed} />
      <ChangeList title="Node labels changed" items={plan.node_labels_changed} />
      <ElementList title="Relationships added" items={plan.relationships_added} />
      <ElementList title="Relationships removed" items={plan.relationships_removed} />
      <ChangeList title="Relationships changed" items={plan.relationships_changed} />
      <ObjectList title="Constraints and indexes to create" items={plan.objects_to_create} />
      <ObjectList title="Constraints and indexes to drop" items={plan.objects_to_drop} />
    </div>
  );
}

function ElementList({ title, items }: { title: string; items: readonly string[] }) {
  if (items.length === 0) return null;
  return (
    <section>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
      <ul className="mt-1 flex flex-col gap-0.5 text-sm text-slate-800">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  );
}

function ChangeList({
  title,
  items,
}: {
  title: string;
  items: readonly { readonly element: string; readonly detail: string }[];
}) {
  if (items.length === 0) return null;
  return (
    <section>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
      <ul className="mt-1 flex flex-col gap-1 text-sm">
        {items.map((item) => (
          <li key={item.element}>
            <span className="font-medium text-slate-900">{item.element}</span>
            <span className="block text-slate-600">{item.detail}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ObjectList({
  title,
  items,
}: {
  title: string;
  items: readonly {
    readonly kind: string;
    readonly label: string;
    readonly properties: readonly string[];
    readonly detail: string;
  }[];
}) {
  if (items.length === 0) return null;
  return (
    <section>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
      <ul className="mt-1 flex flex-col gap-0.5 text-sm">
        {items.map((item) => (
          <li key={`${item.kind}-${item.label}-${item.properties.join(",")}-${item.detail}`}>
            <span className="font-medium text-slate-900">{item.label}</span>
            <span className="text-slate-600"> ({item.properties.join(", ")})</span>
            {/* Derived from identity or asked for in a draft: which family a
                line belongs to decides who owns fixing it. */}
            <span className="ml-2 font-mono text-xs text-slate-500">{item.kind}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * Properties, Mapping and Indexes -- three views over one shape payload.
 *
 * One query, three projections, rather than three fetches: they are fields on
 * the same document, and the shared query key means switching tabs re-renders
 * rather than refetches.
 */
function ShapeTab({ draftId, tab }: { draftId: string; tab: Tab }) {
  const shape = useQuery({
    queryKey: ["graph-schema", "shape", draftId],
    queryFn: () => graphSchemaApi.getDraftShape(draftId),
    staleTime: SHAPE_STALE_TIME_MS,
  });

  if (shape.isLoading) return <p className="text-sm text-slate-500">Loading...</p>;
  if (shape.error) return <p className="text-sm text-red-700">{shape.error.message}</p>;

  const entities = Object.entries(shape.data?.entities ?? {});

  if (tab === "Indexes") {
    const indexes = shape.data?.graph_indexes ?? [];
    const constraints = shape.data?.graph_constraints ?? [];
    if (indexes.length === 0 && constraints.length === 0) {
      return <p className="text-sm text-slate-600">No indexes or constraints defined.</p>;
    }
    return (
      <div className="flex flex-col gap-3 text-sm">
        <ul className="flex flex-col gap-1">
          {indexes.map((index) => (
            <li key={`${index.label}-${index.properties.join(",")}`}>
              <span className="font-medium text-slate-900">{index.label}</span>
              <span className="text-slate-600"> ({index.properties.join(", ")})</span>
            </li>
          ))}
        </ul>
        <ul className="flex flex-col gap-1">
          {constraints.map((constraint) => (
            <li key={`${constraint.label}-${constraint.property_name}`}>
              <span className="font-medium text-slate-900">{constraint.label}</span>
              <span className="text-slate-600">.{constraint.property_name}</span>
              {/* Unique and required are separate guarantees; a constraint can
                  be either, both, or neither, so both are stated. */}
              <span className="ml-2 text-xs text-slate-500">
                {constraint.unique ? "unique " : ""}
                {constraint.required ? "required" : ""}
              </span>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  if (entities.length === 0) {
    return <p className="text-sm text-slate-600">This draft has no entities yet.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="p-2">Entity</th>
            <th className="p-2">Property</th>
            {tab === "Properties" ? <th className="p-2">Type</th> : null}
            {tab === "Mapping" ? <th className="p-2">Source field</th> : null}
            {tab === "Mapping" ? <th className="p-2">Transformation</th> : null}
          </tr>
        </thead>
        <tbody>
          {entities.flatMap(([label, entity]) =>
            Object.entries(entity.properties).map(([name, property]) => (
              <tr key={`${label}.${name}`} className="border-t border-slate-200">
                <td className="p-2 text-slate-900">{label}</td>
                <td className="p-2 text-slate-800">{name}</td>
                {tab === "Properties" ? <td className="p-2 text-slate-600">{property.type}</td> : null}
                {tab === "Mapping" ? (
                  <td className="p-2 font-mono text-xs text-slate-600">
                    {/* An unmapped property is a real state -- a derived field,
                        or one a mutation added without a source. Blank would
                        read as missing data. */}
                    {property.source_field ?? "unmapped"}
                  </td>
                ) : null}
                {tab === "Mapping" ? (
                  <td className="p-2 text-slate-600">{property.transformation ?? "NONE"}</td>
                ) : null}
              </tr>
            )),
          )}
        </tbody>
      </table>
    </div>
  );
}

function ValidationTab({
  draftId,
  canApprove,
  onChanged,
}: {
  draftId: string;
  canApprove: boolean;
  onChanged: () => void;
}) {
  const [result, setResult] = useState<readonly ValidationFindingView[] | null>(null);
  const [passed, setPassed] = useState<boolean | null>(null);
  const [approved, setApproved] = useState(false);

  const validate = useMutation({
    mutationFn: () => graphSchemaApi.validateDraft(draftId),
    onSuccess: (data) => {
      setResult(data.findings);
      setPassed(data.passed);
      onChanged();
    },
  });

  const approve = useMutation({
    mutationFn: () => graphSchemaApi.approveDraft(draftId),
    onSuccess: () => {
      setApproved(true);
      onChanged();
    },
  });

  // Publishing is what makes an approved schema the one the platform runs.
  // Separate from approving because they are separate decisions, and because
  // a shape can be approved and still fail to compile -- which comes back as
  // `accepted: false` with the element named, not as an error.
  const publish = useMutation({
    mutationFn: (activate: boolean) => graphSchemaApi.publishDraft(draftId, activate),
    onSuccess: onChanged,
  });

  return (
    <div>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => { validate.mutate(); }}
          disabled={validate.isPending}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-800 disabled:opacity-50"
        >
          Validate
        </button>
        <button
          type="button"
          onClick={() => { approve.mutate(); }}
          // Approval is gated on a passing validation *and* the capability.
          // The backend refuses either way; this avoids offering it.
          disabled={!canApprove || passed !== true || approve.isPending}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300"
        >
          Approve
        </button>
        <button
          type="button"
          onClick={() => { publish.mutate(false); }}
          // Only after an approval in this session. The backend refuses an
          // unapproved draft either way; offering the button would invite a
          // 409 that reads like a bug.
          disabled={!canApprove || !approved || publish.isPending}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-800 disabled:opacity-50"
        >
          Publish release
        </button>
        <button
          type="button"
          onClick={() => { publish.mutate(true); }}
          disabled={!canApprove || !approved || publish.isPending}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300"
        >
          Publish and activate
        </button>
      </div>

      {validate.error ? (
        <p className="mt-3 text-sm text-red-700">{validate.error.message}</p>
      ) : null}
      {approve.error ? (
        <p className="mt-3 text-sm text-red-700">{approve.error.message}</p>
      ) : null}
      {publish.error ? (
        <p className="mt-3 text-sm text-red-700">{publish.error.message}</p>
      ) : null}
      {publish.data ? (
        // A refused compilation is reported as plainly as a successful one:
        // "published" and "could not be compiled" are both answers, and only
        // the second tells the analyst what to change.
        <p
          className={`mt-3 text-sm ${publish.data.accepted ? "text-slate-900" : "text-red-700"}`}
        >
          {publish.data.accepted
            ? `Released as ${publish.data.configurationReleaseId}${
                publish.data.detail === "activated" ? " and now live." : "."
              }`
            : `Not published: ${publish.data.detail ?? "the shape could not be compiled."}`}
        </p>
      ) : null}

      {passed !== null ? (
        <p className="mt-3 text-sm font-medium text-slate-900">
          {passed ? "Validation passed." : "Validation failed."}
        </p>
      ) : null}

      {result && result.length > 0 ? (
        <ul className="mt-3 flex flex-col gap-2">
          {result.map((finding, index) => (
            <li key={`${finding.check}-${finding.element}-${String(index)}`} className="text-sm">
              <span className="font-mono text-xs text-slate-500">{finding.severity}</span>{" "}
              <span className="font-medium text-slate-900">{finding.element}</span>
              <span className="block text-slate-600">{finding.message}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function VersionsTab({ draftId }: { draftId: string }) {
  const revisions = useQuery({
    queryKey: ["graph-schema", "revisions", draftId],
    queryFn: () => graphSchemaApi.listRevisions(draftId),
  });

  if (revisions.isLoading) return <p className="text-sm text-slate-500">Loading...</p>;
  if (revisions.error) {
    return <p className="text-sm text-red-700">{revisions.error.message}</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="py-2 pr-4">Seq</th>
            <th className="py-2 pr-4">Author</th>
            <th className="py-2 pr-4">By model</th>
            <th className="py-2 pr-4">Mutations</th>
            <th className="py-2">Created</th>
          </tr>
        </thead>
        <tbody>
          {(revisions.data ?? []).map((revision) => (
            <tr key={revision.revision_id} className="border-t border-slate-200">
              <td className="py-2 pr-4">{revision.sequence}</td>
              <td className="py-2 pr-4">{revision.author}</td>
              {/* Whether a revision was authored by a model is provenance an
                  operator needs when judging a proposal, so it is shown rather
                  than folded into the author string. */}
              <td className="py-2 pr-4">{revision.authored_by_model ? "yes" : "no"}</td>
              <td className="py-2 pr-4">{revision.mutation_count}</td>
              <td className="py-2">{new Date(revision.created_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

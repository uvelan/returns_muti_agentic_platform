import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  graphSchemaApi,
  type AnalysisSessionView,
  type ValidationFindingView,
} from "../../api/graphSchema";
import { useCapabilities } from "../../hooks/capabilityContext";

/**
 * The Graph Schema Analyzer screen (Phase 20).
 *
 * **The required layout is Sources | Graph Canvas | Analyzer Copilot, and the
 * canvas is deliberately absent.** `/api/graph-schema` exposes a draft's
 * `entity_count` and `relationship_count` and nothing else -- `draft.shape.
 * entities` exists in the domain model but is never serialized by any route.
 * There is no endpoint that returns the entities and relationships a canvas
 * would draw. Rendering a plausible-looking graph from counts would be
 * fabrication, so the canvas column states the gap instead. Closing it is a
 * backend change (serialize the shape), not a frontend one.
 *
 * **Never offer a source-side schema modification.** Nothing in this screen
 * writes to a source; the Sources column is strictly read-only, which is also
 * all the API would permit.
 */

const TABS = ["Validation", "Versions", "Properties", "Mapping", "Indexes", "Sync"] as const;
type Tab = (typeof TABS)[number];

/** Tabs the analyzer API has no data for. Named, not silently omitted. */
const UNBACKED_TABS: Partial<Record<Tab, string>> = {
  Properties: "Per-entity properties are not exposed by /api/graph-schema.",
  Mapping: "Source-to-entity mappings are not exposed by /api/graph-schema.",
  Indexes: "Index definitions are not exposed by /api/graph-schema.",
  Sync: "Generation build, activation and sync are lifecycle operations outside this surface.",
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

      <p className="mt-6 border-t border-slate-200 pt-4 text-sm text-slate-500">
        The node-and-edge canvas is not rendered: <code>/api/graph-schema</code> returns
        entity and relationship <em>counts</em> only, never the shape itself. Drawing a
        graph from counts would be invented structure. Serializing the draft shape is a
        backend change.
      </p>
    </section>
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
  onChanged,
}: {
  tab: Tab;
  draftId: string | null;
  canApprove: boolean;
  onChanged: () => void;
}) {
  const unbacked = UNBACKED_TABS[tab];
  if (unbacked) {
    return <p className="text-sm text-slate-500">{unbacked}</p>;
  }
  if (draftId === null) {
    return <p className="text-sm text-slate-600">Select an analysis with a draft.</p>;
  }
  return tab === "Validation" ? (
    <ValidationTab draftId={draftId} canApprove={canApprove} onChanged={onChanged} />
  ) : (
    <VersionsTab draftId={draftId} />
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
      </div>

      {validate.error ? (
        <p className="mt-3 text-sm text-red-700">{validate.error.message}</p>
      ) : null}
      {approve.error ? (
        <p className="mt-3 text-sm text-red-700">{approve.error.message}</p>
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

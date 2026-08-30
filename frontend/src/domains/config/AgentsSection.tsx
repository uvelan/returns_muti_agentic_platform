import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2 } from "lucide-react";
import { Link } from "wouter";

import { agentConfigApi, type AgentSummary } from "../../api/agentConfig";
import { useCapabilities } from "../../hooks/capabilityContext";
import { DocumentEditor, type Json, type JsonObject } from "./DocumentEditor";

/**
 * Per-agent configuration, editable as nested key/value, split view, or JSON.
 *
 * Each agent already had its own module file; nothing served it. This is that
 * surface: pick an agent, then edit the same document as raw JSON or through a
 * key/value editor that follows the document's own nesting.
 *
 * The editor itself now lives in `DocumentEditor.tsx`, because the support
 * template needs the same one. What stays here is what is specific to an agent:
 * the registry list, and the write path -- a `PUT` that submits a governance
 * proposal rather than changing anything.
 */

function configurationSourceLabel(source: string | undefined): string {
  if (source === undefined) return "Source unknown";
  if (source === "RELEASE") return "Active release";
  if (source === "PACKAGED_BASELINE") return "Packaged baseline";
  return source.replaceAll("_", " ").toLowerCase();
}


export function AgentsSection() {
  const [selected, setSelected] = useState<string | null>(null);
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
  const agents = useQuery({ queryKey: ["config", "agents"], queryFn: () => agentConfigApi.list() });

  if (agents.error !== null) {
    return (
      <p role="alert" className="text-sm text-error">
        {agents.error.message}
      </p>
    );
  }
  if (agents.isPending) return <p className="text-sm text-on-surface-variant">Loading...</p>;

  const list = agents.data;
  // First by default, so the pane is never an empty frame beside a full list.
  const active = selected ?? (list.length > 0 ? list[0].manifestId : null);
  function selectAgent(manifestId: string) {
    if (manifestId === active) return;
    if (
      hasUnsavedChanges
      && !window.confirm("Discard unsaved configuration changes and open another agent?")
    ) return;
    setHasUnsavedChanges(false);
    setSelected(manifestId);
  }


  return (
    // The registry track was a hard `20rem`, which needs 320px for itself at
    // a 320px viewport. The mock never populates agents, so the route sweep
    // passed this vacuously -- it stacks below `lg` now.
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-[20rem_minmax(0,1fr)]">
      <aside className="premium-panel self-start overflow-hidden">
        <header className="border-b border-outline-variant/80 bg-surface-container-low px-4 py-3">
          <p className="premium-kicker">Agent registry</p>
          <div className="mt-1 flex items-end justify-between gap-3">
            <h2 className="text-base font-semibold text-on-surface">Configured agents</h2>
            <span className="rounded-full bg-secondary-container px-2 py-0.5 text-[11px] font-semibold text-on-secondary-container">
              {list.length}
            </span>
          </div>
        </header>
        <ul className="flex max-h-[42rem] flex-col gap-1.5 overflow-y-auto p-2">
          {list.map((agent) => (
            <li key={agent.manifestId}>
              <button
                type="button"
                onClick={() => { selectAgent(agent.manifestId); }}
                aria-current={agent.manifestId === active ? "true" : undefined}
                className={[
                  "flex w-full items-start gap-3 rounded-xl border px-3 py-3 text-left transition",
                  agent.manifestId === active
                    ? "border-primary/30 bg-secondary-container shadow-sm"
                    : "border-transparent hover:border-outline-control hover:bg-surface-container-low",
                ].join(" ")}
              >
                <span aria-hidden="true" className={[
                  "mt-1.5 size-2 shrink-0 rounded-full",
                  agent.enabled ? "bg-primary" : "bg-outline-variant",
                ].join(" ")} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold text-on-surface">
                    {agent.name}
                  </span>
                  <span className="mt-1 block truncate font-mono text-[10px] text-outline">
                    {agent.moduleId}
                  </span>
                  <span className="mt-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wide text-on-surface-variant">
                    <span>{agent.status.replaceAll("_", " ")}</span>
                    <span aria-hidden="true">&middot;</span>
                    <span>v{agent.configurationVersion}</span>
                    <span aria-hidden="true">&middot;</span>
                    <span>{agent.enabled ? "Enabled" : "Disabled"}</span>
                  </span>
                  <span className="mt-1 block text-[10px] font-semibold text-primary">{configurationSourceLabel(agent.source)}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </aside>
      {active === null ? (
        <div className="premium-panel flex min-h-64 items-center justify-center text-sm text-on-surface-variant">
          No agents are configured.
        </div>
      ) : (
        <AgentEditor key={active} manifestId={active} onDirtyChange={setHasUnsavedChanges} />
      )}
    </div>
  );
}

/**
 * Fetches, then hands the loaded document to the editor.
 *
 * Split in two so the editor can seed its state from a prop in `useState`
 * rather than copying the query result across in an effect. An effect that
 * writes state renders once with the wrong value and again with the right one,
 * and here the wrong value is "no document" -- which would blank an operator's
 * in-progress edit every time the query refetched.
 */
function AgentEditor({
  manifestId,
  onDirtyChange,
}: {
  manifestId: string;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const { can } = useCapabilities();
  const configuration = useQuery({
    queryKey: ["config", "agents", manifestId],
    queryFn: () => agentConfigApi.read(manifestId),
  });

  if (configuration.error !== null) {
    return (
      <p role="alert" className="text-sm text-error">
        {configuration.error.message}
      </p>
    );
  }
  if (configuration.isPending) {
    return <p className="text-sm text-on-surface-variant">Loading...</p>;
  }
  return (
    <DocumentEditor
      kicker="Agent configuration"
      subtitle={configuration.data.path}
      badges={
        <span className="rounded-full bg-secondary-container px-2 py-0.5 text-on-secondary-container">
          {configurationSourceLabel(configuration.data.source)}
        </span>
      }
      loaded={configuration.data.document as Json}
      canWrite={can("governance.proposal.write")}
      jsonLabel="Agent configuration JSON"
      submitLabel="Submit for review"
      submittingLabel="Submitting..."
      submitTitle="Proposal write access is required"
      readOnlyNotice="Read-only access. Proposal write permission is required to change this configuration."
      notObjectMessage="An agent configuration must be an object."
      onSubmit={async (document: JsonObject) => {
        const proposal = await agentConfigApi.save(manifestId, document);
        await queryClient.invalidateQueries({ queryKey: ["proposals"] });
        return proposal;
      }}
      renderResult={(proposal) => (
        <div role="status" className="flex items-center justify-between gap-4 rounded-xl border border-primary/20 bg-secondary-container px-4 py-3 text-sm text-on-secondary-container">
          <span className="flex items-center gap-2">
            <CheckCircle2 size={16} aria-hidden="true" />
            Proposal {proposal.proposalId} is {proposal.status.replaceAll("_", " ").toLowerCase()}.
            The active configuration has not changed.
          </span>
          <Link href="/approvals" className="shrink-0 font-semibold text-primary hover:underline">
            Open Approvals
          </Link>
        </div>
      )}
      onDirtyChange={onDirtyChange}
    />
  );
}

export type { AgentSummary };

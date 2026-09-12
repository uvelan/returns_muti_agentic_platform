import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Braces, CheckCircle2, ListTree } from "lucide-react";
import { Link } from "wouter";

import {
  agentConfigApi,
  type AgentConfigurationProposal,
  type AgentSummary,
} from "../../api/agentConfig";
import { configApi } from "../../api/configuration";
import { EnumSelect, type EnumOption } from "../../components/forms/EnumSelect";
import { Toggle } from "../../components/forms/Toggle";
import { useCapabilities } from "../../hooks/capabilityContext";
import { DocumentEditor, type Json, type JsonObject } from "./DocumentEditor";
import { asObject, asString } from "./jsonPath";

/**
 * `/config/agents` -- CFG-5b's repoint of the console's own Agents screen at
 * the live `RETURN_PLATFORM.agents` section (D-CFG-1). Each agent already had
 * its own module file under `backend/config/agents/`; nothing at runtime read
 * it (`AgentRegistry.build()` and every agent class read only
 * `ReturnPlatformConfiguration.agents["<id>"]`), so the manifest system was
 * the console's own invention. `AgentConfigurationView.document` is now that
 * small, fully-typed shape, so a typed table is a direct fit rather than a
 * simplification of something richer.
 *
 * **A typed table, on `TypedSectionScreen`'s own chrome, but not the
 * component itself.** `TypedSectionScreen` publishes a domain patch straight
 * onto the release (`POST /api/config/publish`) -- the right shape for the
 * screens that own one `RETURN_PLATFORM` section slice. An agent edit is not
 * that: `PUT /api/agents/{id}` files a governance proposal and the release
 * does not move until `/approvals` activates it. Reusing the header layout,
 * the Advanced/Typed toggle and the `fieldset`-gated disabled state keeps the
 * screen looking like the rest of Configuration; the save path underneath it
 * stays the proposal path W4.2 built, unchanged.
 *
 * **Each row owns its own document and its own Save.** `PUT` is per agent,
 * not per table, so there is no single page-level publish action -- a row
 * that is dirty can be saved without touching any other row. The typed
 * fields (`name`, `version`, `enabled`, `ai_assisted`, `ai_route_ref`) are
 * read from and written back into that row's own full document, never a
 * hand-built subset, so a dead knob a previous edit set (`timeout_seconds`,
 * say) travels through a typed-table save unchanged instead of silently
 * resetting to its default.
 */

function configurationSourceLabel(source: string | undefined): string {
  if (source === undefined) return "Source unknown";
  if (source === "RELEASE") return "Active release";
  return source.replaceAll("_", " ").toLowerCase();
}

/** `ai_route_ref`'s options: every AI gateway task id the runtime snapshot
 * carries, plus the submitted value itself if it names a task the release no
 * longer lists (`EnumSelect`'s own `allowUnknown` handles the display; this
 * just has to make sure "no route" is always a selectable option too). */
function aiGatewayTaskOptions(runtime: Readonly<Record<string, unknown>> | undefined): EnumOption[] {
  const gateway = asObject(runtime?.ai_gateway_configuration as Json | undefined);
  const tasks = asObject(gateway.tasks);
  const ids = Object.keys(tasks).sort((a, b) => a.localeCompare(b));
  return [
    { value: "", label: "None" },
    ...ids.map((id) => ({ value: id, label: id })),
  ];
}

export function AgentsSection() {
  const [advanced, setAdvanced] = useState(false);
  const { can } = useCapabilities();
  const canWrite = can("governance.proposal.write");
  const agents = useQuery({ queryKey: ["config", "agents"], queryFn: () => agentConfigApi.list() });
  const runtime = useQuery({ queryKey: ["config", "runtime"], queryFn: configApi.runtime });

  if (agents.error !== null) {
    return (
      <p role="alert" className="text-sm text-error">
        {agents.error.message}
      </p>
    );
  }
  if (agents.isPending) return <p className="text-sm text-on-surface-variant">Loading...</p>;

  const list = agents.data;
  const routeOptions = aiGatewayTaskOptions(runtime.data);

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="premium-kicker">Agent registry</p>
          <h2 className="mt-0.5 text-base font-semibold text-on-surface">Configured agents</h2>
          <p className="mt-1 max-w-3xl text-sm text-on-surface-variant">
            The live agents every case runs against. Saving a row files a configuration proposal --
            it does not change what is running until an approver activates it on Approvals.
          </p>
        </div>
        <button
          type="button"
          onClick={() => { setAdvanced((value) => !value); }}
          aria-pressed={advanced}
          className="flex shrink-0 items-center gap-1.5 rounded-lg border border-outline-control bg-surface-container-lowest px-3 py-2 text-xs font-medium text-on-surface-variant transition hover:border-primary hover:text-primary"
        >
          {advanced ? <ListTree size={13} aria-hidden="true" /> : <Braces size={13} aria-hidden="true" />}
          {advanced ? "Typed form" : "Advanced (JSON)"}
        </button>
      </header>

      {!canWrite ? (
        <p className="rounded-lg border border-outline-variant bg-surface-container-low px-3 py-2 text-sm text-on-surface-variant">
          Read-only access. Proposal write permission is required to change this configuration.
        </p>
      ) : null}

      {list.length === 0 ? (
        <div className="premium-panel flex min-h-64 items-center justify-center text-sm text-on-surface-variant">
          No agents are configured.
        </div>
      ) : advanced ? (
        <AdvancedAgentEditor agents={list} canWrite={canWrite} />
      ) : (
        <TypedAgentTable agents={list} canWrite={canWrite} routeOptions={routeOptions} />
      )}
    </div>
  );
}

function TypedAgentTable({
  agents,
  canWrite,
  routeOptions,
}: {
  agents: readonly AgentSummary[];
  canWrite: boolean;
  routeOptions: readonly EnumOption[];
}) {
  return (
    <div className="premium-panel overflow-hidden">
      <div className="overflow-x-auto">
        <fieldset disabled={!canWrite} className="w-full min-w-[52rem] disabled:opacity-75">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-outline-variant/80 bg-surface-container-low text-left text-[11px] font-semibold uppercase tracking-wide text-on-surface-variant">
                <th scope="col" className="px-3 py-2.5">Agent</th>
                <th scope="col" className="px-3 py-2.5">Name</th>
                <th scope="col" className="px-3 py-2.5">Version</th>
                <th scope="col" className="px-3 py-2.5">Enabled</th>
                <th scope="col" className="px-3 py-2.5">AI-assisted</th>
                <th scope="col" className="px-3 py-2.5">AI route</th>
                <th scope="col" className="px-3 py-2.5">
                  <span className="sr-only">Save</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-outline-variant/60">
              {agents.map((summary) => (
                <AgentRow
                  key={summary.manifestId}
                  manifestId={summary.manifestId}
                  routeOptions={routeOptions}
                />
              ))}
            </tbody>
          </table>
        </fieldset>
      </div>
    </div>
  );
}

function AgentRow({
  manifestId,
  routeOptions,
}: {
  manifestId: string;
  routeOptions: readonly EnumOption[];
}) {
  const configuration = useQuery({
    queryKey: ["config", "agents", manifestId],
    queryFn: () => agentConfigApi.read(manifestId),
  });

  if (configuration.error !== null) {
    return (
      <tr>
        <td colSpan={7} className="px-3 py-2.5">
          <p role="alert" className="text-sm text-error">{configuration.error.message}</p>
        </td>
      </tr>
    );
  }
  if (configuration.isPending) {
    return (
      <tr>
        <td colSpan={7} className="px-3 py-2.5 text-sm text-on-surface-variant">
          Loading {manifestId}...
        </td>
      </tr>
    );
  }
  return (
    <AgentRowLoaded
      key={JSON.stringify(configuration.data.document)}
      manifestId={manifestId}
      source={configuration.data.source}
      loaded={configuration.data.document as JsonObject}
      routeOptions={routeOptions}
    />
  );
}

function AgentRowLoaded({
  manifestId,
  source,
  loaded,
  routeOptions,
}: {
  manifestId: string;
  source: string;
  loaded: JsonObject;
  routeOptions: readonly EnumOption[];
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<JsonObject>(loaded);
  const [proposal, setProposal] = useState<AgentConfigurationProposal | null>(null);
  // RV round 1, A4: the baseline `dirty` compares against. Starts as `loaded`
  // and moves to whatever was just saved on success, so Save disables itself
  // the moment a proposal is filed rather than staying enabled on an
  // unchanged draft -- a second click with nothing new typed would file a
  // second, identical proposal an approver then has to notice and reject.
  // Moves again, away from the saved snapshot, the instant `set()` runs, so
  // one more edit is all it takes to re-enable Save for a genuinely new
  // change.
  const [savedSnapshot, setSavedSnapshot] = useState<JsonObject>(loaded);

  const save = useMutation({
    mutationFn: () => agentConfigApi.save(manifestId, draft),
    onSuccess: async (result) => {
      setProposal(result);
      setSavedSnapshot(draft);
      await queryClient.invalidateQueries({ queryKey: ["proposals"] });
    },
  });

  function set(key: string, value: Json) {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setProposal(null);
    save.reset();
  }

  const editableKeys = ["name", "version", "enabled", "ai_assisted", "ai_route_ref"] as const;
  const dirty = editableKeys.some(
    (key) => JSON.stringify(draft[key] ?? null) !== JSON.stringify(savedSnapshot[key] ?? null),
  );

  const name = asString(draft.name);
  const version = asString(draft.version);
  const enabled = draft.enabled === true;
  const aiAssisted = draft.ai_assisted === true;
  const aiRouteRef = typeof draft.ai_route_ref === "string" ? draft.ai_route_ref : "";

  return (
    <tr className="align-top">
      <td className="px-3 py-3">
        <span className="block font-mono text-xs text-on-surface">{manifestId}</span>
        <span className="mt-1 block text-[10px] font-semibold text-primary">
          {configurationSourceLabel(source)}
        </span>
      </td>
      <td className="px-3 py-3">
        <label className="sr-only" htmlFor={`${manifestId}-name`}>Name</label>
        <input
          id={`${manifestId}-name`}
          type="text"
          value={name}
          onChange={(event) => { set("name", event.target.value); }}
          className="premium-field w-full py-1.5 text-sm"
        />
      </td>
      <td className="px-3 py-3">
        <label className="sr-only" htmlFor={`${manifestId}-version`}>Version</label>
        <input
          id={`${manifestId}-version`}
          type="text"
          value={version}
          onChange={(event) => { set("version", event.target.value); }}
          className="premium-field w-24 py-1.5 text-sm"
        />
      </td>
      <td className="px-3 py-3">
        <Toggle label="Enabled" value={enabled} onChange={(next) => { set("enabled", next); }} />
      </td>
      <td className="px-3 py-3">
        <Toggle
          label="AI-assisted"
          value={aiAssisted}
          onChange={(next) => { set("ai_assisted", next); }}
        />
      </td>
      <td className="px-3 py-3">
        <EnumSelect
          label="AI route"
          id={`${manifestId}-ai-route-ref`}
          value={aiRouteRef}
          options={routeOptions}
          onChange={(next) => { set("ai_route_ref", next === "" ? null : next); }}
        />
      </td>
      <td className="px-3 py-3">
        <div className="flex flex-col items-start gap-1.5">
          <button
            type="button"
            onClick={() => { save.mutate(); }}
            disabled={!dirty || save.isPending}
            // RV round 1, A1 (carrying CFG-5's own H1/H2): `disabled:opacity-40`
            // on `bg-primary`/`text-on-primary` composited to 2.12:1 -- the same
            // family of defect CFG-5's F1 was blocking on, and its own advisory
            // said explicitly to carry the fix into this screen's Save button.
            // Dimmed through a non-text channel instead, so the label itself
            // stays high-contrast in both states.
            className="rounded-lg border border-transparent bg-primary px-3 py-2 text-xs font-semibold text-on-primary shadow-sm transition hover:brightness-105 disabled:cursor-not-allowed disabled:border-outline-variant disabled:bg-surface-container-low disabled:text-on-surface-variant disabled:shadow-none disabled:hover:brightness-100"
          >
            {save.isPending ? "Submitting..." : "Save"}
          </button>
          {save.error instanceof Error ? (
            <p role="alert" className="max-w-[16rem] text-xs text-error">{save.error.message}</p>
          ) : null}
          {proposal !== null ? (
            <div role="status" className="flex max-w-[16rem] flex-col gap-1 text-xs text-on-surface-variant">
              <span className="flex items-center gap-1 text-secondary">
                <CheckCircle2 size={13} aria-hidden="true" />
                Proposal {proposal.proposalId} is {proposal.status.replaceAll("_", " ").toLowerCase()}.
              </span>
              <span>The active configuration has not changed.</span>
              <Link href="/approvals" className="font-semibold text-primary hover:underline">
                Open Approvals
              </Link>
            </div>
          ) : null}
        </div>
      </td>
    </tr>
  );
}

/**
 * The pre-CFG-5b master-detail JSON editor, kept as the Advanced escape
 * hatch: pick an agent, edit its whole document as JSON. Unchanged in
 * behaviour from before this lease -- only the field names in the mock/real
 * document shape moved, not this editor's own logic.
 */
function AdvancedAgentEditor({
  agents,
  canWrite,
}: {
  agents: readonly AgentSummary[];
  canWrite: boolean;
}) {
  const [selected, setSelected] = useState<string>(agents[0]?.manifestId ?? "");
  const queryClient = useQueryClient();
  const configuration = useQuery({
    queryKey: ["config", "agents", selected],
    queryFn: () => agentConfigApi.read(selected),
    enabled: selected !== "",
  });

  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-[16rem_minmax(0,1fr)]">
      <label className="flex flex-col gap-1.5">
        <span className="premium-kicker">Agent</span>
        <select
          value={selected}
          onChange={(event) => { setSelected(event.target.value); }}
          className="premium-field text-sm"
        >
          {agents.map((agent) => (
            <option key={agent.manifestId} value={agent.manifestId}>
              {agent.name} ({agent.manifestId})
            </option>
          ))}
        </select>
      </label>
      {configuration.error !== null ? (
        <p role="alert" className="text-sm text-error">{configuration.error.message}</p>
      ) : configuration.isPending ? (
        <p className="text-sm text-on-surface-variant">Loading...</p>
      ) : (
        <DocumentEditor
          key={selected}
          kicker="Agent configuration"
          subtitle={configuration.data.path}
          badges={
            <span className="rounded-full bg-secondary-container px-2 py-0.5 text-on-secondary-container">
              {configurationSourceLabel(configuration.data.source)}
            </span>
          }
          loaded={configuration.data.document as Json}
          canWrite={canWrite}
          jsonLabel="Agent configuration JSON"
          submitLabel="Submit for review"
          submittingLabel="Submitting..."
          submitTitle="Proposal write access is required"
          readOnlyNotice="Read-only access. Proposal write permission is required to change this configuration."
          notObjectMessage="An agent configuration must be an object."
          onSubmit={async (document: JsonObject) => {
            const proposal = await agentConfigApi.save(selected, document);
            await queryClient.invalidateQueries({ queryKey: ["proposals"] });
            await queryClient.invalidateQueries({ queryKey: ["config", "agents"] });
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
          onDirtyChange={() => { /* no page-level dirty tracking in Advanced mode */ }}
        />
      )}
    </div>
  );
}

export type { AgentSummary };

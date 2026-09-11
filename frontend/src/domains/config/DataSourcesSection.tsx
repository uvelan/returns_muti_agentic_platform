import { useState } from "react";
import { skipToken, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Database, History, Play, UserRound } from "lucide-react";

import { graphSyncApi, type StartSyncInput, type SyncRun } from "../../api/graphSync";
import {
  CONNECTOR_TYPES,
  sourceBindingsApi,
  type RebindInput,
  type SourceBinding,
} from "../../api/sourceBindings";
import { EnumSelect } from "../../components/forms/EnumSelect";
import { FieldGroup } from "../../components/forms/FieldGroup";
import { KeyValueTable, type KeyValueEntry } from "../../components/forms/KeyValueTable";
import { useCapabilities } from "../../hooks/capabilityContext";
import { formatTimestamp } from "../../format/datetime";
import { TextField } from "./TextField";

/**
 * `/config/source-bindings` -- where each dataset the active schema names is
 * actually read from, and the sync that keeps the graph current.
 *
 * **Named distinctly from the Analyzer's own "Data Sources" section
 * (`/graph-schema/data-sources`) -- audit finding D1.** The brief's own
 * design table calls this route `/config/data-sources`, but `registry.ts`
 * removed exactly that label from `CONFIG_SECTIONS` once already
 * (`"Data Sources" is deliberately absent... It is now the Graph Schema
 * Analyzer's Data Sources section"`), for the reason D1 names: one label
 * meaning two different screens is what the audit flagged. Reusing it here
 * would restore precisely that collision, one lease after it was resolved.
 * "Source Bindings" is what this section is -- overrides on where a dataset
 * is read from, plus the sync that reads it -- and it does not collide with
 * anything the Analyzer already owns.
 *
 * **This screen does not ride `TypedSectionScreen`.** Source bindings are
 * not part of a configuration release: `PUT`/`DELETE /api/source-bindings/{dataset}`
 * write immediately, the same direct-write shape `sourceBindings.ts`'s own
 * docstring describes -- there is no draft, no Validate, no Publish. Gated
 * on `config.source.rebind`, not `config.release.write`.
 *
 * **The sync trigger and run history moved here from `/sync`, unchanged in
 * behaviour.** `SyncControlPage.tsx`'s `DomainRail`-based summary is
 * replaced with an inline panel matching this screen's own chrome -- a
 * portal into a rail slot this nested tab does not have would render
 * nothing, which is what actually prompted the swap, not a stylistic
 * preference. `/sync` itself now redirects here (`domainScreens.ts`).
 */

export function DataSourcesSection() {
  return (
    <div className="flex flex-col gap-4">
      <header>
        <p className="premium-kicker">Data Sources</p>
        <h2 className="mt-0.5 text-base font-semibold text-on-surface">Source Bindings</h2>
        <p className="mt-1 max-w-3xl text-sm text-on-surface-variant">
          Where each dataset the active schema names is actually read from, and the sync that keeps
          the graph current from it. Distinct from the Graph Schema Analyzer's own Data Sources
          section, which analyzes source shape rather than binding or syncing it.
        </p>
      </header>
      <SourceBindingsPanel />
      <SyncPanel />
    </div>
  );
}

// --- source bindings -----------------------------------------------------

function SourceBindingsPanel() {
  const { can } = useCapabilities();
  const queryClient = useQueryClient();
  const bindings = useQuery({
    queryKey: ["source-bindings"],
    queryFn: () => sourceBindingsApi.list(),
  });
  const canRebind = can("config.source.rebind");

  const rebind = useMutation({
    mutationFn: ({ dataset, input }: { dataset: string; input: RebindInput }) =>
      sourceBindingsApi.rebind(dataset, input),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["source-bindings"] });
    },
  });
  const clear = useMutation({
    mutationFn: (dataset: string) => sourceBindingsApi.clear(dataset),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["source-bindings"] });
    },
  });

  return (
    <FieldGroup
      kicker="Source bindings"
      title="Where each dataset is read from"
      description="The declared asset per dataset, and any deliberate override. Writes immediately -- there is no draft or release for a binding."
    >
      {bindings.error !== null ? (
        <p role="alert" className="text-sm text-error">{bindings.error.message}</p>
      ) : bindings.isPending ? (
        <p className="text-sm text-on-surface-variant">Loading...</p>
      ) : bindings.data.length === 0 ? (
        <p className="text-sm text-on-surface-variant">No datasets are bound yet.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {bindings.data.map((binding) => (
            <BindingRow
              key={binding.dataset}
              binding={binding}
              canRebind={canRebind}
              onRebind={(input) => { rebind.mutate({ dataset: binding.dataset, input }); }}
              onClear={() => { clear.mutate(binding.dataset); }}
              rebinding={rebind.isPending}
              clearing={clear.isPending}
            />
          ))}
        </ul>
      )}
      {rebind.error instanceof Error ? (
        <p role="alert" className="text-sm text-error">{rebind.error.message}</p>
      ) : null}
      {clear.error instanceof Error ? (
        <p role="alert" className="text-sm text-error">{clear.error.message}</p>
      ) : null}
    </FieldGroup>
  );
}

function BindingRow({
  binding,
  canRebind,
  onRebind,
  onClear,
  rebinding,
  clearing,
}: {
  binding: SourceBinding;
  canRebind: boolean;
  onRebind: (input: RebindInput) => void;
  onClear: () => void;
  rebinding: boolean;
  clearing: boolean;
}) {
  const [editing, setEditing] = useState(false);

  return (
    <li className="flex flex-col gap-2 rounded-lg border border-outline-variant/70 bg-surface-container-low p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-sm font-semibold text-on-surface">
            {binding.dataset}
            {binding.overridden ? (
              <span className="rounded-full bg-secondary-container px-2 py-0.5 text-[10px] font-semibold uppercase text-on-secondary-container">
                Overridden
              </span>
            ) : null}
          </p>
          <p className="mt-0.5 text-xs text-on-surface-variant">
            {binding.connectorType} · {binding.sourceAssetId}
            {binding.incrementalCursorField !== null ? ` · cursor: ${binding.incrementalCursorField}` : ""}
          </p>
          <p className="mt-0.5 font-mono text-[11px] text-outline">
            {Object.entries(binding.objectRef).map(([key, value]) => `${key}=${value}`).join(", ")}
          </p>
        </div>
        {canRebind ? (
          <div className="flex shrink-0 gap-2">
            <button
              type="button"
              onClick={() => { setEditing((value) => !value); }}
              className="rounded-lg border border-outline-control bg-surface-container-lowest px-3 py-1.5 text-xs font-medium text-on-surface-variant transition hover:border-primary hover:text-primary"
            >
              {editing ? "Cancel" : "Rebind"}
            </button>
            {binding.overridden ? (
              <button
                type="button"
                disabled={clearing}
                onClick={() => {
                  if (window.confirm(`Clear the override for ${binding.dataset}? It returns to whatever the configured schema says.`)) {
                    onClear();
                  }
                }}
                className="rounded-lg border border-outline-control bg-surface-container-lowest px-3 py-1.5 text-xs font-medium text-on-surface-variant transition hover:border-error hover:text-error disabled:opacity-40"
              >
                {clearing ? "Clearing..." : "Clear"}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
      {editing ? (
        <RebindForm
          binding={binding}
          rebinding={rebinding}
          onSubmit={(input) => {
            onRebind(input);
            setEditing(false);
          }}
          onCancel={() => { setEditing(false); }}
        />
      ) : null}
    </li>
  );
}

function RebindForm({
  binding,
  rebinding,
  onSubmit,
  onCancel,
}: {
  binding: SourceBinding;
  rebinding: boolean;
  onSubmit: (input: RebindInput) => void;
  onCancel: () => void;
}) {
  const [sourceAssetId, setSourceAssetId] = useState(binding.sourceAssetId);
  const [connectorType, setConnectorType] = useState(binding.connectorType);
  const [objectRefEntries, setObjectRefEntries] = useState<KeyValueEntry[]>(
    Object.entries(binding.objectRef).map(([key, value]) => ({ key, value })),
  );
  const [cursorField, setCursorField] = useState(binding.incrementalCursorField ?? "");

  return (
    <form
      className="flex flex-col gap-2 border-t border-outline-variant/60 pt-2"
      onSubmit={(event) => {
        event.preventDefault();
        const objectRef: Record<string, string> = {};
        for (const entry of objectRefEntries) {
          // `valueKind="string"` on the `KeyValueTable` below keeps every row's
          // value a string in practice; a non-string entry.value would be this
          // table used a different way than its own `valueKind` says, which is
          // a defect elsewhere, not a shape this form should guess a rendering
          // for -- serialized rather than blindly `String()`-coerced (which
          // would silently produce "[object Object]" for one) if it ever happens.
          objectRef[entry.key] =
            typeof entry.value === "string" ? entry.value : JSON.stringify(entry.value);
        }
        onSubmit({
          sourceAssetId,
          connectorType,
          objectRef,
          incrementalCursorField: cursorField.trim() === "" ? null : cursorField,
        });
      }}
    >
      <TextField label="Source asset id" value={sourceAssetId} onChange={setSourceAssetId} required />
      <EnumSelect
        label="Connector type"
        value={connectorType}
        onChange={setConnectorType}
        options={CONNECTOR_TYPES.map((value) => ({ value, label: value }))}
      />
      <KeyValueTable
        label="Object reference"
        hint="Connector-specific location -- database/collection for Mongo, schema/table for SQL Server."
        entries={objectRefEntries}
        onChange={setObjectRefEntries}
        valueKind="string"
      />
      <TextField
        label="Incremental cursor field"
        hint="Blank clears it -- the source will not resume, every sync of it rescans."
        value={cursorField}
        onChange={setCursorField}
      />
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={rebinding}
          className="rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-on-primary transition disabled:opacity-40"
        >
          {rebinding ? "Rebinding..." : "Rebind"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg px-3 py-1.5 text-xs font-medium text-on-surface-variant transition hover:text-on-surface"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

// --- sync ------------------------------------------------------------------
//
// Moved from `SyncControlPage.tsx` (`/sync`), which now redirects here. The
// list/detail/trigger logic is unchanged; only the summary panel (previously
// a `DomainRail` portal) is inlined to fit a Configuration tab. RV round 1,
// F4: the design-rationale comments below are the original file's own,
// restored with the code they explain rather than left dropped by the move
// -- they are the record of the defects this screen was built to answer,
// which is the part of a file a move is most likely to lose.
//
// S6's own opening note: the graph the copilot answers from is a projection
// of source systems, and until this screen existed nothing in the console
// said when it was last built, from what, or by whom -- `GraphSyncService`
// recorded every run it ever performed and served that record to nobody
// after Wave F1 unmounted the Data Console.
//
// **Two mechanisms, one history.** A scheduled run covers every
// participating source. A targeted run covers a single record an agent
// pulled in mid-conversation because the graph did not have it yet.
// Splitting them into two lists would mean an operator investigating an
// unexpected node has to know which mechanism to suspect before they can
// look -- so they share the list, and a targeted run says which
// conversation caused it.
//
// **`nodeWrites` is on the card, not buried in the detail.** A sync that
// reported COMPLETED having written nothing is the exact shape of the
// defect this screen shipped alongside: the source answered and the
// projection discarded the answer. That number is what makes it visible.

const FILTERS = [
  { label: "All runs", value: "" },
  { label: "Full", value: "FULL" },
  { label: "Sources", value: "SOURCE_MONGODB" },
  { label: "On demand", value: "ON_DEMAND" },
] as const;

const SCOPES = [
  { label: "Every source", value: "FULL" },
  { label: "MongoDB sources", value: "SOURCE_MONGODB" },
  { label: "SQL Server sources", value: "SQLSERVER" },
] as const;

/**
 * Which records to read, asked separately from which sources to cover.
 *
 * Two questions, not one list of four combinations: the scope above chooses
 * the sources, this chooses how much of each. Phrased as what it does
 * rather than as "full/incremental" -- the operator's question is "does
 * this reread everything", and the answer to that is the whole reason the
 * choice exists.
 */
const READS = [
  { label: "Only what changed since the last run", value: true },
  { label: "Everything, ignoring the last run", value: false },
] as const;

function SyncPanel() {
  const { can } = useCapabilities();
  const client = useQueryClient();
  const [filter, setFilter] = useState<string>("");
  const [selected, setSelected] = useState<string | null>(null);

  const runs = useQuery({
    queryKey: ["graph-sync", "runs", filter],
    queryFn: () => graphSyncApi.listRuns(filter),
    enabled: can("config.source.read"),
  });

  const detail = useQuery({
    queryKey: ["graph-sync", "run", selected],
    queryFn: selected === null ? skipToken : () => graphSyncApi.readRun(selected),
  });

  /**
   * A sync is awaited server-side, so this mutation is pending for as long
   * as the run takes. That is deliberate: an operator who pressed "Sync
   * now" wants to know the outcome, and a fire-and-forget button that
   * reported success immediately would say "done" about a run that had not
   * started.
   */
  const start = useMutation({
    mutationFn: (input: StartSyncInput) => graphSyncApi.startRun(input),
    onSuccess: async (run) => {
      setSelected(run.id);
      await client.invalidateQueries({ queryKey: ["graph-sync", "runs"] });
    },
  });

  if (!can("config.source.read")) {
    return (
      <FieldGroup kicker="Sync" title="Graph sync">
        <p className="text-sm text-on-surface-variant">You do not have access to source sync.</p>
      </FieldGroup>
    );
  }

  // The newest run in whatever the list currently holds. `listRuns` returns
  // newest first, so this is the head rather than a scan -- and it is the
  // answer to the only question this domain exists to answer, which is
  // whether the graph is current.
  const newest = (runs.data ?? []).at(0) ?? null;

  return (
    <FieldGroup
      kicker="Sync"
      title="Graph sync"
      description="What each sync read from the sources, and what it wrote to the graph. A scheduled run covers every participating source; a targeted run covers a single record an agent pulled in mid-conversation."
    >
      <div className="grid grid-cols-1 gap-3 rounded-lg border border-outline-variant/70 bg-surface-container-low p-3 sm:grid-cols-2 lg:grid-cols-5">
        <SummaryFact label="Latest status" value={runs.isPending ? null : (newest?.status ?? "None yet")} />
        <SummaryFact label="Mode" value={newest?.mode ?? null} />
        <SummaryFact label="Records" value={newest?.recordScope ?? null} />
        <SummaryFact label="Started" value={newest?.startedAt !== undefined ? formatTimestamp(newest.startedAt) : null} />
        <SummaryFact label="Runs listed" value={runs.isPending ? null : String((runs.data ?? []).length)} />
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <RunListPane
          filter={filter}
          onFilterChange={setFilter}
          runs={runs.data ?? []}
          error={runs.error}
          loading={runs.isPending}
          selected={selected}
          onSelect={setSelected}
          canStart={can("config.source.write")}
          onStart={(input) => { start.mutate(input); }}
          starting={start.isPending}
          startError={start.error}
        />
        <RunDetailPane run={detail.data ?? null} loading={selected !== null && detail.isPending} />
      </div>
    </FieldGroup>
  );
}

function SummaryFact({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <p className="text-[10px] font-semibold uppercase tracking-wide text-outline">{label}</p>
      <p className="text-sm text-on-surface">{value ?? "-"}</p>
    </div>
  );
}

function Pane({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-outline-variant bg-surface-container-lowest">
      <h3 className="border-b border-outline-variant px-4 py-3 text-sm font-semibold text-on-surface">
        {title}
      </h3>
      {children}
    </section>
  );
}

function StatusPill({ status }: { status: SyncRun["status"] }) {
  // STALLED reads as a failure, because operationally it is one: the run
  // stopped reporting and the graph may hold a partial rebuild. It is a
  // separate status rather than FAILED because the responses differ --
  // FAILED points at the source data, STALLED points at the worker -- but
  // neither is a run an operator should read as still in progress.
  //
  // Before this, a run whose process died stayed RUNNING forever and the
  // pill said so. One had been RUNNING for fifteen hours with zero node
  // writes.
  const tone =
    status === "FAILED" || status === "STALLED"
      ? "border-error text-error"
      : status === "RUNNING"
        ? "border-primary text-primary"
        : "border-outline-variant text-on-surface-variant";
  return (
    <span className={`rounded-full border px-1.5 py-0.5 text-[10px] uppercase ${tone}`}>
      {status}
    </span>
  );
}

function RunListPane({
  filter,
  onFilterChange,
  runs,
  error,
  loading,
  selected,
  onSelect,
  canStart,
  onStart,
  starting,
  startError,
}: {
  filter: string;
  onFilterChange: (value: string) => void;
  runs: readonly SyncRun[];
  error: Error | null;
  loading: boolean;
  selected: string | null;
  onSelect: (id: string) => void;
  canStart: boolean;
  onStart: (input: StartSyncInput) => void;
  starting: boolean;
  startError: Error | null;
}) {
  return (
    <Pane title="Sync runs">
      <div className="flex flex-wrap gap-1.5 border-b border-outline-variant px-3 py-2">
        {FILTERS.map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => { onFilterChange(option.value); }}
            className={`rounded-full border px-3 py-1 text-xs transition ${
              filter === option.value
                ? "border-primary text-primary"
                : "border-outline-control text-on-surface-variant hover:border-primary hover:text-primary"
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>

      {canStart ? <StartSyncForm onSubmit={onStart} starting={starting} error={startError} /> : null}

      <div className="max-h-96 flex-1 overflow-y-auto">
        {/*
          Three states, not two. `data ?? []` would tell an operator the
          graph has never been synchronized when the truth is that we could
          not ask -- and "never synchronized" is something they would act on.
        */}
        {error !== null ? (
          <p role="alert" className="px-4 py-3 text-sm text-error">{error.message}</p>
        ) : loading ? (
          <p className="px-4 py-3 text-sm text-on-surface-variant">Loading...</p>
        ) : runs.length === 0 ? (
          <p className="flex items-center gap-2 px-4 py-3 text-sm text-on-surface-variant">
            <History size={15} aria-hidden="true" />
            No sync runs recorded.
          </p>
        ) : (
          <ul>
            {runs.map((run) => (
              <li key={run.id}>
                <button
                  type="button"
                  onClick={() => { onSelect(run.id); }}
                  className={`flex w-full flex-col gap-1 border-b border-outline-variant px-4 py-2.5 text-left transition hover:bg-surface-container ${
                    run.id === selected ? "bg-surface-container" : ""
                  }`}
                >
                  <span className="flex items-center gap-2">
                    <span aria-hidden="true" className="text-on-surface-variant">
                      {run.mode === "ON_DEMAND" ? <Bot size={14} /> : <Database size={14} />}
                    </span>
                    <span className="truncate text-sm text-on-surface">{run.mode}</span>
                    <StatusPill status={run.status} />
                    {/*
                      Only the incremental case is badged. A full scan is the
                      default and marking every row "FULL" would make the one
                      distinction that matters harder to spot, not easier.
                    */}
                    {run.recordScope === "INCREMENTAL" ? (
                      <span className="rounded-full border border-outline-variant px-1.5 py-0.5 text-[10px] uppercase text-on-surface-variant">
                        Incremental
                      </span>
                    ) : null}
                  </span>
                  <span className="flex flex-wrap items-center gap-x-3 text-[11px] text-outline">
                    <span>{formatTimestamp(run.startedAt)}</span>
                    <span>{run.startedBy}</span>
                    {/*
                      The number that distinguishes a sync that worked from
                      one that reported success and wrote nothing.
                    */}
                    <span>{run.nodeWrites} nodes</span>
                    {run.errorCode !== null ? <span className="text-error">{run.errorCode}</span> : null}
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

/**
 * Start a sync.
 *
 * Collapsed until asked for. Re-reading production sources and rewriting
 * the graph the copilot answers from is not a button that should sit
 * permanently armed next to a list an operator is scrolling.
 */
function StartSyncForm({
  onSubmit,
  starting,
  error,
}: {
  onSubmit: (input: StartSyncInput) => void;
  starting: boolean;
  error: Error | null;
}) {
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState<StartSyncInput["mode"]>("FULL");
  // Defaults to the full scan, matching the backend. A manual "Sync now" is
  // usually pressed *because* something looks wrong with the graph, and
  // resuming from a cursor is the wrong default for that.
  const [incremental, setIncremental] = useState(false);
  const [maxRecords, setMaxRecords] = useState("1000");

  if (!open) {
    return (
      <div className="border-b border-outline-variant px-3 py-2">
        <button
          type="button"
          onClick={() => { setOpen(true); }}
          className="flex items-center gap-1.5 text-xs text-primary transition hover:underline"
        >
          <Play size={14} aria-hidden="true" />
          Sync now
        </button>
        {error !== null ? (
          // Kept visible after the form closes: a refused sync that
          // vanished with the form would read as one that ran.
          <p role="alert" className="mt-1 text-[11px] text-error">{error.message}</p>
        ) : null}
      </div>
    );
  }

  return (
    <form
      className="flex flex-col gap-2 border-b border-outline-variant px-3 py-2.5"
      onSubmit={(event) => {
        event.preventDefault();
        const parsed = Number.parseInt(maxRecords, 10);
        onSubmit({
          mode: scope,
          incremental,
          // Omitted rather than sent as NaN if the field was cleared; the
          // backend has its own default and its own ceiling.
          ...(Number.isFinite(parsed) && parsed > 0 ? { maxRecordsPerAsset: parsed } : {}),
        });
      }}
    >
      {error !== null ? <p role="alert" className="text-sm text-error">{error.message}</p> : null}
      <label className="flex flex-col gap-1 text-[11px] text-outline">
        Scope
        <select
          value={scope}
          onChange={(event) => {
            // Matched against the declared scopes rather than asserted: the
            // element hands back a `string`, and an assertion here would
            // let a renamed option through as a mode the backend rejects.
            const chosen = SCOPES.find((option) => option.value === event.target.value);
            if (chosen !== undefined) setScope(chosen.value);
          }}
          className="rounded border border-outline-control bg-surface px-2 py-1.5 text-sm text-on-surface outline-none transition focus:border-primary focus:ring-1 focus:ring-primary"
        >
          {SCOPES.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-[11px] text-outline">
        Read
        <select
          value={incremental ? "incremental" : "full"}
          onChange={(event) => { setIncremental(event.target.value === "incremental"); }}
          className="rounded border border-outline-control bg-surface px-2 py-1.5 text-sm text-on-surface outline-none transition focus:border-primary focus:ring-1 focus:ring-primary"
        >
          {READS.map((option) => (
            <option key={option.label} value={option.value ? "incremental" : "full"}>{option.label}</option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-[11px] text-outline">
        Records per source
        <input
          value={maxRecords}
          inputMode="numeric"
          onChange={(event) => { setMaxRecords(event.target.value); }}
          className="rounded border border-outline-control bg-surface px-2 py-1.5 text-sm text-on-surface outline-none transition focus:border-primary focus:ring-1 focus:ring-primary"
        />
      </label>
      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={starting}
          className="rounded bg-primary px-3 py-1.5 text-xs text-on-primary transition disabled:opacity-40"
        >
          {starting ? "Syncing..." : "Start sync"}
        </button>
        <button
          type="button"
          onClick={() => { setOpen(false); }}
          className="text-xs text-on-surface-variant transition hover:text-on-surface"
        >
          Cancel
        </button>
      </div>
      {starting ? (
        <p role="status" className="text-[11px] text-on-surface-variant">
          Reading the sources. This finishes when the run does.
        </p>
      ) : null}
    </form>
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

function RunDetailPane({ run, loading }: { run: SyncRun | null; loading: boolean }) {
  if (loading) {
    return (
      <Pane title="Run">
        <p className="px-4 py-3 text-sm text-on-surface-variant">Loading...</p>
      </Pane>
    );
  }

  if (run === null) {
    return (
      <Pane title="Run">
        <div className="flex flex-1 items-center justify-center p-6 text-center">
          <p className="max-w-xs text-sm text-on-surface-variant">
            Pick a run to see what it read and what it wrote.
          </p>
        </div>
      </Pane>
    );
  }

  const sources = Object.entries(run.sourceCounts);

  return (
    <Pane title={`${run.mode} run`}>
      <div className="flex max-h-96 flex-1 flex-col gap-5 overflow-y-auto p-4">
        <section className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <StatusPill status={run.status} />
            <span className="text-[11px] text-outline">{run.id}</span>
          </div>
          <Facts
            rows={[
              ["Started", formatTimestamp(run.startedAt)],
              ["Finished", formatTimestamp(run.completedAt)],
              ["Started by", run.startedBy],
              ["Schema", run.schemaVersion],
              ["Read", run.recordScope === "INCREMENTAL" ? "Only what changed" : "Everything"],
              ...(run.graphGenerationId === null ? [] : ([["Generation", run.graphGenerationId]] as const)),
              ...(run.errorCode === null ? [] : ([["Error", run.errorCode]] as const)),
            ]}
          />
        </section>

        <section className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-outline">Wrote</h3>
          <Facts
            rows={[
              ["Nodes", String(run.nodeWrites)],
              ["Relationships", String(run.relationshipWrites)],
            ]}
          />
          {/*
            Deliberately not shown for an incremental run. Writing nothing
            is the *expected* outcome there -- it means no source changed
            since the last run -- and an error-toned warning on every quiet
            run is how a real one stops being read.
          */}
          {run.status === "COMPLETED" && run.nodeWrites === 0 && run.recordScope !== "INCREMENTAL" ? (
            // The specific failure that hid behind a green status: the
            // source answered and nothing reached the graph. Said plainly
            // rather than left for someone to notice a zero.
            <p role="status" className="text-xs text-error">
              This run completed without writing anything. Either the sources had nothing new, or
              what came back did not project.
            </p>
          ) : null}
        </section>

        {run.skippedSources === undefined || run.skippedSources.length === 0 ? null : (
          <section className="flex flex-col gap-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-outline">Skipped</h3>
            {/*
              A source with no cursor field cannot be resumed, so an
              incremental run passes over it entirely and still reports
              COMPLETED. Left unsaid, that source silently stops syncing
              until someone runs a full scan.
            */}
            <p className="text-xs text-error">
              These sources have no cursor and were not read. They stay as the last full sync left
              them.
            </p>
            <ul className="flex flex-col gap-0.5 text-xs text-on-surface">
              {run.skippedSources.map((source) => <li key={source}>{source}</li>)}
            </ul>
          </section>
        )}

        <section className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-outline">Read</h3>
          {sources.length === 0 ? (
            <p className="text-xs text-on-surface-variant">No source records were read.</p>
          ) : (
            <Facts rows={sources.map(([source, count]) => [source, String(count)] as const)} />
          )}
        </section>

        {run.requestedBy === null ? null : (
          <section className="flex flex-col gap-2">
            <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-outline">
              <UserRound size={13} aria-hidden="true" />
              Requested by an agent turn
            </h3>
            {/*
              What a targeted run is for. An operator seeing a sync nobody
              started needs the conversation, not just a timestamp -- and the
              anchor's *fields*, because the anchor's values are a customer's
              order number and this list is exported and kept.
            */}
            <Facts
              rows={[
                ["Agent", run.requestedBy.agentId],
                ["Conversation", run.requestedBy.conversationId],
                ["Turn", run.requestedBy.clientTurnId],
                ["Entity", run.requestedBy.entityId],
                ["Anchor", run.requestedBy.strongAnchorId],
                ["Anchor fields", run.requestedBy.anchorFieldIds.join(", ")],
              ]}
            />
          </section>
        )}

        {run.constraintsApplied.length === 0 ? null : (
          <section className="flex flex-col gap-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-outline">
              Constraints applied
            </h3>
            <ul className="flex flex-col gap-0.5 text-xs text-on-surface">
              {run.constraintsApplied.map((constraint) => <li key={constraint}>{constraint}</li>)}
            </ul>
          </section>
        )}
      </div>
    </Pane>
  );
}

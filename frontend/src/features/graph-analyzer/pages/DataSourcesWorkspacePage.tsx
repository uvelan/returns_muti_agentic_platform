import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ChevronRight,
  CircleAlert,
  Database,
  Eye,
  FileKey,
  Layers,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Table2,
  Trash2,
} from "lucide-react";

import { previewSourceObject, refreshSource, removeSource, saveSource, validateSource } from "../../../api/graphAnalyzer";
import { dataSourcesApi } from "../../../api/dataSources";
import type { AnalyzerSource, SourceInput, SourceObject } from "../../../contracts/graphAnalyzer";
import { AnalyzerLayout } from "../components/AnalyzerLayout";
import { ConfirmationDialog } from "../components/ConfirmationDialog";
import { SourceDialog } from "../components/SourceDialog";
import { analyzerKeys, useAnalyzerBootstrap, useAnalyzerMutation } from "../analyzerQueries";
import { DataView, StructureView } from "../components/ObjectViews";

/**
 * Connection configuration and inspection, as a drill-down.
 *
 * This replaced the `/data-sources` domain. Two screens had grown the same
 * capability -- that one and the analyzer's Connections panel both managed the
 * same connections -- and rather than relocate the weaker screen, its job moved
 * onto the better one and it was deleted.
 *
 * Four levels, one click apart, with a breadcrumb back:
 *
 *     Connections -> Tables/Collections -> Schema -> Data
 *
 * **No analysis selection here.** Choosing what to analyze is the Graph
 * Analyzer section's job, and putting checkboxes on both screens is how a user
 * ends up unsure which selection the Analyze button reads.
 */

type Level = "CONNECTIONS" | "OBJECTS" | "SCHEMA" | "DATA";

export function DataSourcesWorkspacePage() {
  const bootstrap = useAnalyzerBootstrap();
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [objectId, setObjectId] = useState<string | null>(null);
  const [viewingData, setViewingData] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<AnalyzerSource | null>(null);
  const [pendingDelete, setPendingDelete] = useState<AnalyzerSource | null>(null);

  const save = useAnalyzerMutation(
    ({ input, id }: { readonly input: SourceInput; readonly id?: string }) => saveSource(input, id),
  );
  const remove = useAnalyzerMutation(removeSource);
  const validate = useAnalyzerMutation(validateSource);
  const refresh = useAnalyzerMutation(refreshSource);

  const sources = bootstrap.data?.sources ?? [];
  const source = sources.find((item) => item.id === sourceId) ?? null;
  const objects = useMemo(() => (source === null ? [] : leaves(source.objects)), [source]);
  const object = objects.find((item) => item.id === objectId) ?? null;

  const level: Level =
    source === null ? "CONNECTIONS" : object === null ? "OBJECTS" : viewingData ? "DATA" : "SCHEMA";

  const busy = validate.isPending || refresh.isPending || remove.isPending;

  return (
    <AnalyzerLayout>
      <div className="space-y-5">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[.2em] text-emerald-500">
              Connections
            </p>
            <h2 className="mt-1 text-2xl font-semibold text-white">
              Configure sources. Inspect what they hold.
            </h2>
            <p className="mt-1 max-w-2xl text-sm text-analyzer-on-surface-variant">
              Add, validate and edit read-only connections, then drill in to see what each one
              exposes. Choosing what to analyze happens in Graph Analyzer.
            </p>
          </div>
          <button
            type="button"
            onClick={() => {
              setEditing(null);
              setDialogOpen(true);
            }}
            className="inline-flex items-center gap-2 rounded-lg bg-analyzer-primary px-4 py-2.5 text-sm font-semibold text-analyzer-on-primary shadow-lg shadow-black/20 hover:bg-emerald-300"
          >
            <Plus size={16} />
            Add data source
          </button>
        </header>

        <Breadcrumb
          source={source}
          object={object}
          level={level}
          onConnections={() => {
            setSourceId(null);
            setObjectId(null);
            setViewingData(false);
          }}
          onObjects={() => {
            setObjectId(null);
            setViewingData(false);
          }}
          onSchema={() => {
            setViewingData(false);
          }}
        />

        {level === "CONNECTIONS" ? (
          <>
            <PlatformHealthStrip />
            <ConnectionsLevel
              sources={sources}
              loading={bootstrap.isLoading}
              error={bootstrap.isError ? bootstrap.error.message : null}
              busy={busy}
              onRetry={() => {
                void bootstrap.refetch();
              }}
              onOpen={(item) => {
                setSourceId(item.id);
                setObjectId(null);
                setViewingData(false);
              }}
              onEdit={(item) => {
                setEditing(item);
                setDialogOpen(true);
              }}
              onDelete={setPendingDelete}
              onValidate={(item) => {
                validate.mutate(item.id);
              }}
              onRefresh={(item) => {
                refresh.mutate(item.id);
              }}
            />
          </>
        ) : null}

        {level === "OBJECTS" && source !== null ? (
          <ObjectsLevel
            source={source}
            objects={objects}
            refreshing={refresh.isPending}
            onRefresh={() => {
              refresh.mutate(source.id);
            }}
            onOpen={(item) => {
              setObjectId(item.id);
              setViewingData(false);
            }}
          />
        ) : null}

        {(level === "SCHEMA" || level === "DATA") && source !== null && object !== null ? (
          <ObjectLevel
            source={source}
            object={object}
            showingData={level === "DATA"}
            onViewData={() => {
              setViewingData(true);
            }}
            onViewSchema={() => {
              setViewingData(false);
            }}
          />
        ) : null}
      </div>

      <SourceDialog
        key={`${String(dialogOpen)}:${editing?.id ?? "new"}`}
        source={editing}
        open={dialogOpen}
        saving={save.isPending}
        error={save.error?.message ?? null}
        onClose={() => {
          if (!save.isPending) setDialogOpen(false);
        }}
        onSave={(input) => {
          save.mutate(
            { input, id: editing?.id },
            {
              onSuccess: () => {
                setDialogOpen(false);
              },
            },
          );
        }}
      />
      <ConfirmationDialog
        isOpen={pendingDelete !== null}
        title="Remove source configuration?"
        description={`${pendingDelete?.name ?? "This source"} will be removed from Analyzer selections and mappings. No database, schema, or business record in the source will be deleted.`}
        confirmText={remove.isPending ? "Removing…" : "Remove configuration"}
        isDestructive
        onCancel={() => {
          if (!remove.isPending) setPendingDelete(null);
        }}
        onConfirm={() => {
          if (pendingDelete !== null && !remove.isPending) {
            remove.mutate(pendingDelete.id, {
              onSuccess: () => {
                setPendingDelete(null);
                if (sourceId === pendingDelete.id) {
                  setSourceId(null);
                  setObjectId(null);
                }
              },
            });
          }
        }}
      />
    </AnalyzerLayout>
  );
}

/** Every selectable leaf under a source, flattened for the object list. */
function leaves(nodes: readonly SourceObject[]): readonly SourceObject[] {
  return nodes.flatMap((node) => (node.children.length > 0 ? leaves(node.children) : [node]));
}

function Breadcrumb({
  source,
  object,
  level,
  onConnections,
  onObjects,
  onSchema,
}: {
  readonly source: AnalyzerSource | null;
  readonly object: SourceObject | null;
  readonly level: Level;
  readonly onConnections: () => void;
  readonly onObjects: () => void;
  readonly onSchema: () => void;
}) {
  return (
    <nav
      aria-label="Data source breadcrumb"
      className="flex flex-wrap items-center gap-1 rounded-lg border border-analyzer-outline-variant bg-analyzer-surface-container px-3 py-2 text-sm"
    >
      <Crumb label="Connections" active={level === "CONNECTIONS"} onClick={onConnections} />
      {source === null ? null : (
        <>
          <ChevronRight size={14} className="text-analyzer-on-surface-variant" />
          <Crumb label={source.name} active={level === "OBJECTS"} onClick={onObjects} />
        </>
      )}
      {object === null ? null : (
        <>
          <ChevronRight size={14} className="text-analyzer-on-surface-variant" />
          <Crumb label={object.name} active={level === "SCHEMA"} onClick={onSchema} />
        </>
      )}
      {level === "DATA" ? (
        <>
          <ChevronRight size={14} className="text-analyzer-on-surface-variant" />
          <span className="rounded px-2 py-1 font-medium text-analyzer-accent">Data</span>
        </>
      ) : null}
    </nav>
  );
}

function Crumb({
  label,
  active,
  onClick,
}: {
  readonly label: string;
  readonly active: boolean;
  readonly onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={`max-w-64 truncate rounded px-2 py-1 font-medium ${
        active ? "text-analyzer-accent" : "text-analyzer-on-surface-variant hover:bg-white/5 hover:text-white"
      }`}
    >
      {label}
    </button>
  );
}

/**
 * The four platform dependencies, which are a different question from whether
 * one analyzer connection is reachable -- and a different id space, so they are
 * shown as their own strip rather than folded onto the connection cards.
 */
function PlatformHealthStrip() {
  const health = useQuery({ queryKey: ["platform-source-health"], queryFn: () => dataSourcesApi.list() });
  if (health.isLoading || health.isError) return null;
  const items = health.data ?? [];
  if (items.length === 0) return null;
  return (
    <section className="rounded-xl border border-analyzer-outline-variant bg-analyzer-surface-container px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-analyzer-on-surface-variant">
          Platform dependencies
        </span>
        {items.map((item) => (
          <span key={item.id} className="inline-flex items-center gap-1.5 text-xs">
            <span
              className={`size-1.5 rounded-full ${
                item.health === "HEALTHY" ? "bg-analyzer-primary" : "bg-amber-400"
              }`}
            />
            <span className="text-analyzer-on-surface">{item.name}</span>
            <span className="text-analyzer-on-surface-variant">{item.health.toLowerCase()}</span>
          </span>
        ))}
      </div>
    </section>
  );
}

function ConnectionsLevel({
  sources,
  loading,
  error,
  busy,
  onRetry,
  onOpen,
  onEdit,
  onDelete,
  onValidate,
  onRefresh,
}: {
  readonly sources: readonly AnalyzerSource[];
  readonly loading: boolean;
  readonly error: string | null;
  readonly busy: boolean;
  readonly onRetry: () => void;
  readonly onOpen: (source: AnalyzerSource) => void;
  readonly onEdit: (source: AnalyzerSource) => void;
  readonly onDelete: (source: AnalyzerSource) => void;
  readonly onValidate: (source: AnalyzerSource) => void;
  readonly onRefresh: (source: AnalyzerSource) => void;
}) {
  if (loading) {
    return (
      <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
        {[0, 1, 2].map((id) => (
          <div key={id} className="h-32 animate-pulse rounded-xl bg-white/[.035]" />
        ))}
      </div>
    );
  }
  if (error !== null) {
    return (
      <div role="alert" className="rounded-xl border border-red-900 bg-red-950/30 p-6 text-center">
        <CircleAlert className="mx-auto text-red-400" />
        <p className="mt-2 font-medium text-red-100">Configured sources could not be loaded</p>
        <p className="mt-1 text-sm text-red-200/80">{error}</p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-md border border-red-700 px-3 py-1.5 text-sm text-red-100"
        >
          Retry
        </button>
      </div>
    );
  }
  if (sources.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-analyzer-outline p-10 text-center">
        <Database className="mx-auto text-analyzer-on-surface-variant" />
        <p className="mt-2 font-medium text-analyzer-on-surface">No sources configured</p>
        <p className="mt-1 text-sm text-analyzer-on-surface-variant">
          Add a read-only connection to begin discovery.
        </p>
      </div>
    );
  }
  return (
    <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
      {sources.map((source) => (
        <ConnectionCard
          key={source.id}
          source={source}
          busy={busy}
          onOpen={() => {
            onOpen(source);
          }}
          onEdit={() => {
            onEdit(source);
          }}
          onDelete={() => {
            onDelete(source);
          }}
          onValidate={() => {
            onValidate(source);
          }}
          onRefresh={() => {
            onRefresh(source);
          }}
        />
      ))}
    </div>
  );
}

const STATUS_TONE: Record<AnalyzerSource["status"], string> = {
  CONNECTED: "bg-analyzer-primary",
  NOT_VALIDATED: "bg-slate-500",
  VALIDATION_FAILED: "bg-amber-400",
  AUTHENTICATION_FAILED: "bg-red-400",
  UNREACHABLE: "bg-red-400",
};

function ConnectionCard({
  source,
  busy,
  onOpen,
  onEdit,
  onDelete,
  onValidate,
  onRefresh,
}: {
  readonly source: AnalyzerSource;
  readonly busy: boolean;
  readonly onOpen: () => void;
  readonly onEdit: () => void;
  readonly onDelete: () => void;
  readonly onValidate: () => void;
  readonly onRefresh: () => void;
}) {
  return (
    <div className="rounded-xl border border-analyzer-outline-variant bg-analyzer-surface-sunken p-4">
      <button
        type="button"
        onClick={onOpen}
        aria-label={`Open ${source.name}`}
        className="flex w-full items-start gap-3 text-left"
      >
        <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-analyzer-primary-container text-analyzer-accent">
          <Database size={17} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium text-white">{source.name}</span>
          <span className="block truncate text-xs text-analyzer-on-surface-variant">
            {source.engine} · {source.database}
          </span>
          <span className="mt-1.5 flex items-center gap-1.5 text-[11px]">
            <span className={`size-1.5 rounded-full ${STATUS_TONE[source.status]}`} />
            <span className="text-analyzer-on-surface-variant">{source.status.replaceAll("_", " ").toLowerCase()}</span>
            <span className="text-analyzer-on-surface-variant">· {source.objectCount} objects</span>
          </span>
        </span>
        <ChevronRight size={16} className="mt-1 text-analyzer-on-surface-variant" />
      </button>
      <div className="mt-3 flex items-center justify-between border-t border-analyzer-outline-variant pt-3">
        <span className="text-[10px] font-semibold text-emerald-500">READ ONLY</span>
        <div className="flex gap-1">
          <IconButton label="Validate source" disabled={busy} onClick={onValidate}>
            <ShieldCheck size={14} />
          </IconButton>
          <IconButton label="Refresh metadata" disabled={busy} onClick={onRefresh}>
            <RefreshCw size={14} />
          </IconButton>
          <IconButton label="Edit connection" disabled={busy} onClick={onEdit}>
            <Pencil size={14} />
          </IconButton>
          <IconButton label="Remove configuration" disabled={busy} onClick={onDelete}>
            <Trash2 size={14} />
          </IconButton>
        </div>
      </div>
    </div>
  );
}

function IconButton({
  label,
  disabled,
  onClick,
  children,
}: {
  readonly label: string;
  readonly disabled: boolean;
  readonly onClick: () => void;
  readonly children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="rounded-md p-1.5 text-analyzer-on-surface-variant hover:bg-white/5 hover:text-white disabled:opacity-40"
    >
      {children}
    </button>
  );
}

function ObjectsLevel({
  source,
  objects,
  refreshing,
  onRefresh,
  onOpen,
}: {
  readonly source: AnalyzerSource;
  readonly objects: readonly SourceObject[];
  readonly refreshing: boolean;
  readonly onRefresh: () => void;
  readonly onOpen: (object: SourceObject) => void;
}) {
  const [query, setQuery] = useState("");
  const normalized = query.trim().toLowerCase();
  const visible = objects.filter((item) => item.name.toLowerCase().includes(normalized));

  return (
    <section className="rounded-xl border border-analyzer-outline-variant bg-analyzer-surface-container p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="font-semibold text-white">{source.name}</h3>
          <p className="mt-1 text-sm text-analyzer-on-surface-variant">
            {objects.length} object(s) discovered · {source.engine} · read-only
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-3 top-2.5 text-analyzer-on-surface-variant" size={15} />
            <input
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
              }}
              aria-label="Search objects"
              placeholder="Search tables and collections"
              className="w-64 rounded-lg border border-analyzer-outline-control bg-analyzer-surface-container py-2 pl-9 pr-3 text-sm text-white outline-none placeholder:text-analyzer-on-surface-variant focus:border-emerald-600"
            />
          </div>
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing}
            className="inline-flex items-center gap-2 rounded-lg border border-analyzer-outline-control px-3 py-2 text-sm text-emerald-200 disabled:opacity-40"
          >
            <RefreshCw size={14} />
            {refreshing ? "Refreshing…" : "Refresh metadata"}
          </button>
        </div>
      </div>

      {objects.length === 0 ? (
        <div className="mt-5 rounded-lg border border-dashed border-analyzer-outline p-8 text-center">
          <Layers className="mx-auto text-analyzer-on-surface-variant" size={24} />
          <p className="mt-2 font-medium text-analyzer-on-surface">Nothing discovered yet</p>
          <p className="mt-1 text-sm text-analyzer-on-surface-variant">
            Validate the connection to read what this source exposes.
          </p>
        </div>
      ) : visible.length === 0 ? (
        <p className="mt-5 text-sm text-analyzer-on-surface-variant">No object matches “{query}”.</p>
      ) : (
        <ul className="mt-4 grid gap-2 md:grid-cols-2">
          {visible.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => {
                  onOpen(item);
                }}
                aria-label={`Open ${item.name}`}
                className="flex w-full items-center gap-3 rounded-lg border border-analyzer-outline-control bg-analyzer-surface-sunken px-4 py-3 text-left hover:border-analyzer-outline-control"
              >
                <Table2 size={16} className="shrink-0 text-analyzer-primary" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-white">{item.name}</span>
                  <span className="block truncate text-xs text-analyzer-on-surface-variant">
                    {item.kind}
                    {item.fields ? ` · ${String(item.fields.length)} fields` : ""}
                    {item.estimatedRows === null || item.estimatedRows === undefined
                      ? ""
                      : ` · ~${item.estimatedRows.toLocaleString()} rows`}
                  </span>
                </span>
                <ChevronRight size={15} className="shrink-0 text-analyzer-on-surface-variant" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ObjectLevel({
  source,
  object,
  showingData,
  onViewData,
  onViewSchema,
}: {
  readonly source: AnalyzerSource;
  readonly object: SourceObject;
  readonly showingData: boolean;
  readonly onViewData: () => void;
  readonly onViewSchema: () => void;
}) {
  const [page, setPage] = useState(1);
  const [asJson, setAsJson] = useState(false);
  const preview = useQuery({
    queryKey: [...analyzerKeys.all, "preview", source.id, object.id, page],
    queryFn: ({ signal }) => previewSourceObject(source.id, object.id, page, signal),
    enabled: showingData,
  });

  return (
    <section className="overflow-hidden rounded-xl border border-analyzer-outline-variant bg-analyzer-surface-container">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-analyzer-outline-variant px-5 py-4">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="font-semibold text-white">{object.name}</h3>
            <span className="rounded bg-slate-800 px-2 py-0.5 text-[10px] uppercase text-analyzer-on-surface-variant">
              {object.kind}
            </span>
          </div>
          <p className="mt-1 text-xs text-analyzer-on-surface-variant">{object.path.join(" / ")}</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1 rounded-md border border-analyzer-outline px-2 py-1 text-[10px] text-analyzer-accent">
            <ShieldCheck size={12} />
            Read-only
          </span>
          {showingData ? (
            <button
              type="button"
              onClick={onViewSchema}
              className="inline-flex items-center gap-2 rounded-lg border border-analyzer-outline-control px-3 py-2 text-sm text-emerald-200"
            >
              <FileKey size={14} />
              View schema
            </button>
          ) : (
            <button
              type="button"
              onClick={onViewData}
              className="inline-flex items-center gap-2 rounded-lg bg-analyzer-primary px-3 py-2 text-sm font-semibold text-analyzer-on-primary"
            >
              <Eye size={14} />
              View data
            </button>
          )}
        </div>
      </header>

      <div className="min-h-52 p-5">
        {showingData ? (
          <DataView
            asJson={asJson}
            onFormat={setAsJson}
            loading={preview.isLoading}
            error={preview.error?.message ?? null}
            data={preview.data}
            page={page}
            onPage={setPage}
          />
        ) : (
          <StructureView object={object} />
        )}
      </div>
    </section>
  );
}

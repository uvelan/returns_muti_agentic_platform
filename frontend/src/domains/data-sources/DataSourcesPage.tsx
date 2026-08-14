import { useState } from "react";
import { skipToken, useQuery, useQueryClient } from "@tanstack/react-query";
import { Database, RefreshCw } from "lucide-react";

import {
  dataSourcesApi,
  type SourceAssetSummary,
  type SourceDetail,
  type SourceItem,
} from "../../api/dataSources";
import { useCapabilities } from "../../hooks/capabilityContext";
import { SourceBindingsPanel } from "./SourceBindingsPanel";

/**
 * UI-02 -- the Data Sources screen.
 *
 * It was a tab inside Configuration that rendered `/api/config/sources` as raw
 * JSON. That made the platform's whole source surface a nested selection under
 * a domain most of its readers were not visiting: "can we still reach the
 * warehouse database" is asked by people who are not editing configuration, and
 * the answer was three clicks inside a screen about releases.
 *
 * **What this screen offers is what the backend actually supports, and no
 * more.** Health and inventory come from `/api/config/sources`, which re-runs
 * the dependency probe on every request -- so the refresh control *is* the
 * connection test, rather than a separate button calling a route that does not
 * exist. Add / edit / remove exist as dataset *bindings*
 * (`/api/source-bindings`), which is the platform's real unit of change: the
 * four connections themselves are infrastructure the runtime settings name, and
 * a console form that invented a fifth would have nothing to write it to.
 *
 * **No credential is asked for, shown, or sent.** The backend resolves secrets
 * from Vault server-side; `connectionRef` is a `vault://` pointer, and no
 * request or response model on either surface has a field a credential could
 * travel in. A password box here would be a box with nowhere to post.
 */

const HEALTH_ORDER: Readonly<Record<string, number>> = {
  UNAVAILABLE: 0,
  DEGRADED: 1,
  UNKNOWN: 2,
  HEALTHY: 3,
};

export function DataSourcesPage() {
  const { can } = useCapabilities();
  const client = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);

  const canRead = can("config.source.read");

  const sources = useQuery({
    queryKey: ["data-sources"],
    queryFn: () => dataSourcesApi.list(),
    enabled: canRead,
  });

  const detail = useQuery({
    queryKey: ["data-source", selected],
    queryFn: selected === null ? skipToken : () => dataSourcesApi.get(selected),
  });

  if (!canRead) {
    return (
      <p className="text-sm text-on-surface-variant">
        You do not have access to data sources. Viewing them requires config.source.read.
      </p>
    );
  }

  return (
    <div className="grid h-[calc(100vh-3rem)] grid-cols-1 gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <SourceListPane
        sources={sources.data ?? []}
        loading={sources.isPending}
        error={sources.error}
        selected={selected}
        onSelect={setSelected}
        refreshing={sources.isFetching || detail.isFetching}
        onRefresh={() => {
          // Re-probes server-side. `get_sources` fans out to the dependency
          // probes on every call, so this is a live check rather than a cache
          // bust -- which is exactly why there is no separate "test" route.
          void client.invalidateQueries({ queryKey: ["data-sources"] });
          void client.invalidateQueries({ queryKey: ["data-source"] });
        }}
      />
      {/*
        No rebind capability threaded through: `SourceBindingsPanel` asks for
        `config.source.rebind` itself, which is what its two routes require.
        This passed `config.source.write` -- the resync grant -- so a
        `WORKSPACE_EDITOR` was offered a Rebind button the backend refuses.
      */}
      <SourceDetailPane
        source={detail.data ?? null}
        loading={selected !== null && detail.isPending}
        error={detail.error}
      />
    </div>
  );
}

function Pane({
  title,
  action,
  children,
}: {
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-outline-variant bg-surface-container-lowest">
      <div className="flex items-center justify-between gap-2 border-b border-outline-variant px-4 py-3">
        <h2 className="text-sm font-semibold text-on-surface">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

function HealthPill({ health }: { health: string }) {
  const tone =
    health === "UNAVAILABLE"
      ? "border-error text-error"
      : health === "DEGRADED"
        ? "border-primary text-primary"
        : health === "HEALTHY"
          ? "border-outline-variant text-on-surface-variant"
          : // UNKNOWN is not healthy. A probe that could not answer must not
            // look the same as one that answered "fine".
            "border-primary text-primary";
  return (
    <span className={`rounded-full border px-1.5 py-0.5 text-[10px] uppercase ${tone}`}>
      {health}
    </span>
  );
}

function when(value: string | null): string {
  if (value === null) return "-";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function SourceListPane({
  sources,
  loading,
  error,
  selected,
  onSelect,
  refreshing,
  onRefresh,
}: {
  sources: readonly SourceItem[];
  loading: boolean;
  error: Error | null;
  selected: string | null;
  onSelect: (id: string) => void;
  refreshing: boolean;
  onRefresh: () => void;
}) {
  // Worst first. A list of four in declaration order buries the one that is
  // down, and the reason to open this screen is usually that something is.
  const ordered = [...sources].sort(
    (left, right) => (HEALTH_ORDER[left.health] ?? 2) - (HEALTH_ORDER[right.health] ?? 2),
  );

  return (
    <Pane
      title="Sources"
      action={
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          className="flex items-center gap-1.5 text-xs text-primary transition hover:underline disabled:opacity-40"
        >
          <RefreshCw size={13} aria-hidden="true" />
          {refreshing ? "Checking..." : "Re-check"}
        </button>
      }
    >
      <div className="flex-1 overflow-y-auto">
        {/*
          Three states. `data ?? []` would say the platform has no sources
          configured when the truth is that we could not ask -- and "no sources"
          is something an operator would act on.
        */}
        {error !== null ? (
          <p role="alert" className="px-4 py-3 text-sm text-error">
            {error.message}
          </p>
        ) : loading ? (
          <p className="px-4 py-3 text-sm text-on-surface-variant">Loading...</p>
        ) : ordered.length === 0 ? (
          <p className="flex items-center gap-2 px-4 py-3 text-sm text-on-surface-variant">
            <Database size={15} aria-hidden="true" />
            No sources are configured.
          </p>
        ) : (
          <ul>
            {ordered.map((source) => (
              <li key={source.id}>
                <button
                  type="button"
                  onClick={() => {
                    onSelect(source.id);
                  }}
                  className={`flex w-full flex-col gap-1 border-b border-outline-variant px-4 py-2.5 text-left transition hover:bg-surface-container ${
                    source.id === selected ? "bg-surface-container" : ""
                  }`}
                >
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-sm text-on-surface">{source.name}</span>
                    <HealthPill health={source.health} />
                  </span>
                  <span className="flex flex-wrap items-center gap-x-3 text-[11px] text-outline">
                    <span>{source.engine}</span>
                    <span>{source.ownership}</span>
                    {/*
                      READ_ONLY vs WRITABLE is what the platform may do to the
                      source, and it is the difference between a source it can
                      corrupt and one it cannot.
                    */}
                    <span>{source.capability}</span>
                    <span>{source.environment}</span>
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

function AssetTable({ assets }: { assets: readonly SourceAssetSummary[] }) {
  if (assets.length === 0) {
    return (
      <p className="text-xs text-on-surface-variant">
        {/* True of the graph projection, which has no registered assets of its
            own -- its shape lives in the schema registry's graph section. Said
            rather than rendered as an empty table. */}
        No tables or collections are registered for this source.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead className="text-[10px] uppercase tracking-wide text-outline">
          <tr>
            <th className="py-1 pr-3">Object</th>
            <th className="py-1 pr-3">Kind</th>
            <th className="py-1 pr-3">Ownership</th>
            <th className="py-1">Authoritative</th>
          </tr>
        </thead>
        <tbody>
          {assets.map((asset) => (
            <tr key={asset.assetId} className="border-t border-outline-variant">
              <td className="break-all py-1 pr-3 font-mono text-on-surface">{asset.name}</td>
              <td className="py-1 pr-3 text-on-surface-variant">{asset.kind}</td>
              <td className="py-1 pr-3 text-on-surface-variant">{asset.ownership}</td>
              <td className="py-1 text-on-surface-variant">{asset.authoritative ? "Yes" : "No"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SourceDetailPane({
  source,
  loading,
  error,
}: {
  source: SourceDetail | null;
  loading: boolean;
  error: Error | null;
}) {
  if (error !== null) {
    return (
      <Pane title="Source">
        <p role="alert" className="px-4 py-3 text-sm text-error">
          {error.message}
        </p>
      </Pane>
    );
  }

  if (loading) {
    return (
      <Pane title="Source">
        <p className="px-4 py-3 text-sm text-on-surface-variant">Loading...</p>
      </Pane>
    );
  }

  if (source === null) {
    return (
      <Pane title="Source">
        <div className="flex flex-1 flex-col gap-5 overflow-y-auto p-4">
          <p className="text-sm text-on-surface-variant">
            Pick a source to see what it exposes and whether the platform can still reach it.
          </p>
          {/*
            Bindings are shown without a selection because they are the one
            thing on this screen that can be changed, and they are not
            per-source: a dataset names a connection, and the reason to open
            this screen after an infrastructure move is to repoint one.
          */}
          <BindingsSection />
        </div>
      </Pane>
    );
  }

  return (
    <Pane title={source.name}>
      <div className="flex flex-1 flex-col gap-5 overflow-y-auto p-4">
        <section className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <HealthPill health={source.health} />
            <span className="text-[11px] text-outline">{source.id}</span>
          </div>
          <Facts
            rows={[
              ["Engine", source.engine],
              ["Environment", source.environment],
              ["Ownership", source.ownership],
              ["Platform may", source.capability],
              // `engine/database`. Deliberately not a connection string: the
              // backend has no field that could carry one to the browser.
              ["Connection", source.connectionIdentity],
              ["Last probed", when(source.lastInventoryTime)],
              ["Metadata refreshed", when(source.lastMetadataRefresh)],
              [
                "Registered objects",
                String(source.inventoryTotals.assets),
              ],
            ]}
          />
        </section>

        {source.dependencyWarnings.length === 0 ? null : (
          <section className="flex flex-col gap-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-outline">Warnings</h3>
            {/*
              The probe's own `safe_message`. Shown verbatim: it is the only
              thing on the screen that says *why* a source is degraded, and it
              is written to be safe to display.
            */}
            <ul className="flex flex-col gap-0.5 text-xs text-error">
              {source.dependencyWarnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </section>
        )}

        <section className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-outline">
            Tables and collections
          </h3>
          <AssetTable assets={source.assets} />
        </section>

        <BindingsSection />
      </div>
    </Pane>
  );
}

function BindingsSection() {
  return (
    <section className="flex flex-col gap-2 border-t border-outline-variant pt-4">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-outline">
        Dataset bindings
      </h3>
      <p className="text-xs text-on-surface-variant">
        {/*
          Stated on the screen, not only in a code comment: a connection
          reference is a pointer the platform resolves from Vault, and an
          operator who expects to type a password here needs to know why there
          is no box for one.
        */}
        A connection is a <code className="font-mono">vault://</code> reference. The platform
        resolves it server-side, so no credential is ever entered here or returned to this
        browser.
      </p>
      <SourceBindingsPanel />
    </section>
  );
}

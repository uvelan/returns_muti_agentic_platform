import {
  AlertCircle,
  Boxes,
  Database,
  Network,
  RefreshCw,
  TableProperties,
} from "lucide-react";

import { APIError } from "../../../api/client";
import { useUnifiedInventory } from "../../../api/inventoryQueries";


function EmptyEngine({ label }: { readonly label: string }) {
  return (
    <p className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
      {label} inventory is unavailable in this response.
    </p>
  );
}


export function InventoryPage() {
  const { data, error, isError, isFetching, isPending, refetch } =
    useUnifiedInventory();
  const inventory = data?.data ?? null;

  if (isPending) {
    return (
      <div className="flex min-h-[45vh] flex-col items-center justify-center gap-3 text-sm text-slate-600" role="status">
        <h1 className="sr-only">Data Inventory</h1>
        <RefreshCw className="animate-spin motion-reduce:animate-none" size={18} />
        Loading database inventory…
      </div>
    );
  }

  if (isError || inventory === null) {
    return (
      <section className="rounded-xl border border-red-200 bg-red-50 p-8 text-center text-red-950" role="alert">
        <AlertCircle className="mx-auto text-red-600" size={36} />
        <h1 className="mt-3 text-lg font-semibold">Inventory could not be loaded</h1>
        <p className="mt-2 text-sm text-red-800">
          {error instanceof APIError ? error.message : "The inventory request failed."}
        </p>
        <button type="button" onClick={() => void refetch()} className="mt-4 rounded-md bg-white px-4 py-2 text-sm font-medium text-red-700 ring-1 ring-red-300">
          Retry
        </button>
      </section>
    );
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">Data Inventory</h1>
          <p className="mt-1 text-sm text-slate-500">
            Read-only physical metadata from configured SQL Server, MongoDB, and Neo4j services.
          </p>
        </div>
        <button type="button" onClick={() => void refetch()} disabled={isFetching} className="inline-flex items-center gap-2 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60">
          <RefreshCw size={16} className={isFetching ? "animate-spin" : ""} />
          Refresh
        </button>
      </header>

      {data.meta.partial ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900" role="status">
          <p className="font-medium">Partial inventory</p>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {data.meta.warnings.map((warning) => (
              <li key={`${warning.source}:${warning.code}`}>{warning.message}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="flex items-center gap-2 font-semibold text-slate-900"><TableProperties size={18} />SQL Server</h2>
        {inventory.sqlserver === null ? <div className="mt-4"><EmptyEngine label="SQL Server" /></div> : (
          <div className="mt-4 space-y-4">
            <p className="text-sm text-slate-500">Database: <span className="font-medium text-slate-800">{inventory.sqlserver.database_name}</span></p>
            {inventory.sqlserver.schemas.map((schema) => (
              <div key={schema.schema_id} className="rounded-lg border border-slate-200">
                <h3 className="border-b border-slate-200 bg-slate-50 px-4 py-2 text-sm font-semibold">{schema.name}</h3>
                <div className="divide-y divide-slate-100">
                  {[...schema.tables, ...schema.views].map((table) => (
                    <div key={table.object_id} className="flex items-center justify-between gap-4 px-4 py-3 text-sm">
                      <span className="font-medium text-slate-800">{table.name}</span>
                      <span className="text-xs text-slate-500">{table.columns.length} columns{"approximate_row_count" in table ? ` · ${table.approximate_row_count.toLocaleString()} rows` : ""}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="flex items-center gap-2 font-semibold text-slate-900"><Database size={18} />MongoDB</h2>
          {inventory.mongodb === null ? <div className="mt-4"><EmptyEngine label="MongoDB" /></div> : (
            <div className="mt-4 space-y-2">
              {inventory.mongodb.collections.map((collection) => (
                <div key={collection.name} className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2 text-sm">
                  <span className="flex items-center gap-2 font-medium"><Boxes size={15} />{collection.name}</span>
                  <span className="text-xs text-slate-500">{collection.approximate_document_count.toLocaleString()} docs · {collection.indexes.length} indexes</span>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="flex items-center gap-2 font-semibold text-slate-900"><Network size={18} />Neo4j</h2>
          {inventory.neo4j === null ? <div className="mt-4"><EmptyEngine label="Neo4j" /></div> : (
            <div className="mt-4 space-y-4 text-sm">
              <div><h3 className="font-medium text-slate-700">Labels</h3><div className="mt-2 flex flex-wrap gap-2">{inventory.neo4j.labels.map((label) => <span key={label} className="rounded-full bg-sky-50 px-2.5 py-1 text-xs text-sky-800 ring-1 ring-sky-200">{label}</span>)}</div></div>
              <div><h3 className="font-medium text-slate-700">Relationship types</h3><div className="mt-2 flex flex-wrap gap-2">{inventory.neo4j.relationship_types.map((type) => <span key={type} className="rounded-full bg-violet-50 px-2.5 py-1 text-xs text-violet-800 ring-1 ring-violet-200">{type}</span>)}</div></div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

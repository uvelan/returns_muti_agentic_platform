import { useRoute } from "wouter";
import { useInventoryAsset } from "../../../api/inventoryQueries";
import { ErrorState } from "../../../components/ErrorState";
import { LoadingState } from "../../../components/LoadingState";
import { PageHeader } from "../../../components/PageHeader";
import { PropertyList } from "../components/PropertyList";

export function InventoryAssetPage() {
  const [, params] = useRoute("/data-console/inventory/:engine/:assetId");
  const engine = params?.engine ?? "";
  const assetId = params?.assetId ?? "";
  const { data, isLoading, isError, error } = useInventoryAsset(engine, assetId);

  if (isLoading) return <LoadingState message="Loading governed asset metadata..." />;
  if (isError || !data) return <ErrorState title="Inventory asset unavailable" message={error instanceof Error ? error.message : "Asset not found"} />;

  return (
    <div className="p-6">
      <PageHeader title={data.name} description="Read-only governed inventory metadata." />
      <div className="grid gap-6 lg:grid-cols-2">
        <section className="rounded border border-gray-200 bg-white p-5">
          <h2 className="font-semibold">Identity and ownership</h2>
          <div className="mt-4"><PropertyList properties={[
            { label: "Asset ID", value: data.assetId },
            { label: "Engine", value: data.engine },
            { label: "Ownership", value: data.ownership },
            { label: "Capability", value: data.capability },
            { label: "Schema version", value: data.schemaVersion },
            { label: "Record count", value: data.recordCount ?? "Not sampled" },
          ]} /></div>
        </section>
        <section className="rounded border border-gray-200 bg-white p-5">
          <h2 className="font-semibold">Allowed operations</h2>
          <div className="mt-3 flex flex-wrap gap-2">{data.operations.map((operation) => <span key={operation} className="rounded-full bg-slate-100 px-3 py-1 text-sm">{operation}</span>)}</div>
          <h2 className="mt-6 font-semibold">Metadata</h2>
          <pre className="mt-3 overflow-auto rounded bg-gray-950 p-4 text-xs text-gray-100">{JSON.stringify(data.metadata, null, 2)}</pre>
        </section>
      </div>
    </div>
  );
}

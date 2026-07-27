import { useLocation } from "wouter";
import { PageHeader } from "../../../components/PageHeader";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { useBrowserAssets } from "../../../api/browserQueries";
import { DataTable, type ColumnDef } from "../components/DataTable";
import { EngineBadge } from "../components/EngineBadge";
import { type BrowserAsset } from "../../../contracts/browser";

export function BrowserLandingPage() {
  const [, setLocation] = useLocation();
  const { data: assets, isLoading, error } = useBrowserAssets();

  if (isLoading) return <LoadingState message="Loading data browser..." />;
  if (error) {
    return (
      <ErrorState
        title="Failed to load assets"
        message={error instanceof Error ? error.message : "Unknown error"}
      />
    );
  }

  const columns: ColumnDef<BrowserAsset>[] = [
    {
      header: "Asset Name",
      accessor: (asset) => (
        <div>
          <div className="font-medium text-blue-600 hover:underline">{asset.name}</div>
          <div className="text-gray-500 text-xs">ID: {asset.assetId}</div>
        </div>
      ),
    },
    { header: "Engine", accessor: (asset) => <EngineBadge engine={asset.engine} /> },
    { header: "Ownership", accessor: (asset) => asset.ownership },
    { header: "Capability", accessor: (asset) => asset.capability },
    {
      header: "Records",
      accessor: (asset) => asset.recordCount?.toLocaleString() ?? "Probe on browse",
    },
  ];

  return (
    <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8 space-y-6">
      <PageHeader
        title="Governed Data Browser"
        description="Read-only inspection of schema-registry-backed MongoDB and SQL Server assets."
      />

      <div className="bg-white shadow sm:rounded-lg overflow-hidden">
        <div className="px-4 py-5 sm:px-6">
          <h2 className="text-lg leading-6 font-medium text-gray-900">Available Assets</h2>
          <p className="mt-1 max-w-2xl text-sm text-gray-500">
            Select an asset to inspect redacted records. Use AI Studio for governed test-data
            proposals and isolated workspace application.
          </p>
        </div>
        <DataTable
          data={assets ?? []}
          columns={columns}
          keyExtractor={(asset) => `${asset.engine}-${asset.assetId}`}
          onRowClick={(asset) =>
            { setLocation(
              `/data-console/browser/${encodeURIComponent(asset.engine)}/${encodeURIComponent(asset.assetId)}`,
            ); }
          }
          className="border-none shadow-none rounded-none"
        />
      </div>
    </div>
  );
}

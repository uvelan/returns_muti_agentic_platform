import { useLocation } from "wouter";
import { PageHeader } from "../../../components/PageHeader";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { useBrowserAssets } from "../../../api/browserQueries";
import { FixtureNotice } from "../components/FixtureNotice";
import { DataTable, type ColumnDef } from "../components/DataTable";
import { EngineBadge } from "../components/EngineBadge";
import { type BrowserAsset } from "../../../contracts/browser";

export function BrowserLandingPage() {
  const [, setLocation] = useLocation();
  const { data: assets, isLoading, error } = useBrowserAssets();

  if (isLoading) return <LoadingState message="Loading data browser..." />;
  if (error) {
    if (error instanceof Error && error.message.includes("CAPABILITY_ERROR")) {
      return (
        <ErrorState 
          title="Data Browser Unavailable" 
          message="The Data Browser capability requires Fixture Mode. Enable VITE_MOCK_MODE to view this page." 
        />
      );
    }
    return <ErrorState title="Failed to load Assets" message={error instanceof Error ? error.message : "Unknown error"} />;
  }

  const columns: ColumnDef<BrowserAsset>[] = [
    {
      header: "Asset Name",
      accessor: (asset) => (
        <div>
          <div className="font-medium text-blue-600 hover:underline">{asset.name}</div>
          <div className="text-gray-500 text-xs">ID: {asset.assetId}</div>
        </div>
      )
    },
    {
      header: "Engine",
      accessor: (asset) => <EngineBadge engine={asset.engine} />
    },
    {
      header: "Ownership",
      accessor: (asset) => <span className="text-gray-700">{asset.ownership}</span>
    },
    {
      header: "Capability",
      accessor: (asset) => (
        <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
          asset.capability === 'WRITABLE' ? 'bg-orange-100 text-orange-800' : 'bg-gray-100 text-gray-800'
        }`}>
          {asset.capability}
        </span>
      )
    },
    {
      header: "Records",
      accessor: (asset) => <span className="text-gray-700">{asset.recordCount?.toLocaleString() ?? "Unknown"}</span>
    }
  ];

  return (
    <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8 space-y-6">
      <PageHeader 
        title="Governed Data Browser" 
        description="Browse, inspect, and manage data assets securely across governed sources." 
      />
      <FixtureNotice />

      <div className="bg-white shadow sm:rounded-lg overflow-hidden">
        <div className="px-4 py-5 sm:px-6">
          <h2 className="text-lg leading-6 font-medium text-gray-900">Available Assets</h2>
          <p className="mt-1 max-w-2xl text-sm text-gray-500">
            Select an asset to view its schema and records.
          </p>
        </div>
        <DataTable 
          data={assets ?? []} 
          columns={columns} 
          keyExtractor={(asset) => `${asset.engine}-${asset.assetId}`} 
          onRowClick={(asset) => { setLocation(`/data-console/browser/${encodeURIComponent(asset.engine)}/${encodeURIComponent(asset.assetId)}`); }}
          className="border-none shadow-none rounded-none"
        />
      </div>
    </div>
  );
}

import { useState } from "react";
import { useLocation, useParams } from "wouter";
import { PageHeader } from "../../../components/PageHeader";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { Breadcrumbs } from "../../../components/Breadcrumbs";
import { useSourceDetail } from "../../../api/sourceQueries";
import { EngineBadge } from "../components/EngineBadge";
import { Tabs } from "../components/Tabs";
import { PropertyList } from "../components/PropertyList";

export function SourceDetailPage() {
  const params = useParams();
  const [, setLocation] = useLocation();
  const sourceId = (params.sourceId as string) ?? "";
  const { data: source, isLoading, error } = useSourceDetail(sourceId);
  const [activeTab, setActiveTab] = useState("summary");

  if (isLoading) return <LoadingState message="Loading source details..." />;
  if (error || !source) {
    return (
      <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8">
        <Breadcrumbs />
        <ErrorState
          title="Source not found"
          message={error instanceof Error ? error.message : `Unknown source: ${sourceId}`}
        />
      </div>
    );
  }

  const summaryProps = [
    { label: "Connection Identity", value: source.connectionIdentity },
    { label: "Environment", value: source.environment },
    { label: "Engine", value: source.engine },
    { label: "Ownership Classification", value: source.ownership },
    { label: "Access Capability", value: source.capability },
    { label: "Health Status", value: source.health },
    { label: "Inventory Assets", value: source.inventoryTotals.assets, type: "NUMBER" as const },
    {
      label: "Inventory Records",
      value: source.inventoryTotals.records,
      type: "NUMBER" as const,
    },
    {
      label: "Last Dependency Probe",
      value: source.lastMetadataRefresh,
      type: "DATETIME" as const,
    },
  ];

  return (
    <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8 space-y-6">
      <PageHeader
        title={source.name}
        description={`Source ID: ${source.id}`}
      >
        <EngineBadge engine={source.engine} />
      </PageHeader>

      {source.dependencyWarnings.length > 0 && (
        <div className="border-l-4 border-amber-400 bg-amber-50 p-4">
          <h3 className="text-sm font-medium text-amber-800">Dependency warnings</h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-amber-700">
            {source.dependencyWarnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      <Tabs
        activeTab={activeTab}
        onChange={setActiveTab}
        tabs={[
          { id: "summary", label: "Configuration Summary" },
          { id: "assets", label: `Assets (${source.assets.length})` },
          { id: "governance", label: "Governance" },
          { id: "activity", label: "Dependency Evidence" },
        ]}
      />

      {activeTab === "summary" && (
        <div className="bg-white shadow overflow-hidden sm:rounded-lg">
          <PropertyList properties={summaryProps} />
        </div>
      )}

      {activeTab === "assets" && (
        <div className="overflow-hidden rounded-lg bg-white shadow">
          {source.assets.length === 0 ? (
            <div className="p-8 text-center text-gray-500">
              Graph labels and relationships are available in Graph Schema.
            </div>
          ) : (
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  {[
                    "Asset",
                    "Kind",
                    "Ownership",
                    "Authoritative",
                    "Sandbox Write",
                  ].map((heading) => (
                    <th
                      key={heading}
                      className="px-6 py-3 text-left text-xs font-medium uppercase text-gray-500"
                    >
                      {heading}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 bg-white">
                {source.assets.map((asset) => (
                  <tr
                    key={asset.assetId}
                    className="cursor-pointer hover:bg-gray-50"
                    onClick={() =>
                      setLocation(
                        `/data-console/browser/${source.engine === "SQL_SERVER" ? "SQL_SERVER" : "MONGODB"}/${encodeURIComponent(asset.assetId)}`,
                      )
                    }
                  >
                    <td className="px-6 py-4">
                      <div className="font-medium text-gray-900">{asset.name}</div>
                      <div className="text-xs text-gray-500">{asset.assetId}</div>
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-700">{asset.kind}</td>
                    <td className="px-6 py-4 text-sm text-gray-700">{asset.ownership}</td>
                    <td className="px-6 py-4 text-sm text-gray-700">
                      {asset.authoritative ? "Yes" : "No"}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-700">
                      {asset.writableInSandbox ? "AI Studio governed apply" : "Proposal only"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {activeTab === "governance" && (
        <div className="rounded-lg bg-white p-6 shadow space-y-3 text-sm text-gray-700">
          <p>Source browsing is read-only for every asset.</p>
          <p>
            Random or synthetic data is created through AI Studio proposals. Direct apply is
            allowed only for registry-approved sandbox assets.
          </p>
          <p>Authoritative source systems are never mutated by the Data Browser.</p>
        </div>
      )}

      {activeTab === "activity" && (
        <div className="rounded-lg bg-white p-6 shadow">
          <PropertyList
            properties={[
              { label: "Current Health", value: source.health },
              {
                label: "Last Probe Time",
                value: source.lastInventoryTime,
                type: "DATETIME" as const,
              },
              { label: "Warning Count", value: source.dependencyWarnings.length, type: "NUMBER" as const },
            ]}
          />
        </div>
      )}
    </div>
  );
}

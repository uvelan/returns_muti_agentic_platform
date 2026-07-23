/* eslint-disable */
import { useState } from "react";
import { useParams } from "wouter";
import { PageHeader } from "../../../components/PageHeader";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { Breadcrumbs } from "../../../components/Breadcrumbs";
import { useSourceDetail } from "../../../api/sourceQueries";
import { FixtureNotice } from "../components/FixtureNotice";
import { EngineBadge } from "../components/EngineBadge";
import { Tabs } from "../components/Tabs";
import { PropertyList } from "../components/PropertyList";

export function SourceDetailPage() {
  const params = useParams();
  const sourceId = (params.sourceId as string) ?? "";
  const { data: source, isLoading, error } = useSourceDetail(sourceId);
  const [activeTab, setActiveTab] = useState("summary");

  if (isLoading) return <LoadingState message="Loading source details..." />;
  
  if (error) {
    if (error instanceof Error && error.message.includes("CAPABILITY_ERROR")) {
      return (
        <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8">
          <Breadcrumbs />
          <ErrorState 
            title="Data Sources Unavailable" 
            message="The Data Sources capability requires Fixture Mode. Enable VITE_MOCK_MODE to view this page." 
          />
        </div>
      );
    }
    return (
      <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8">
        <Breadcrumbs />
        <ErrorState title="Source Not Found" message={error instanceof Error ? error.message : "Unknown error"} />
      </div>
    );
  }

  if (!source) {
    return (
      <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8">
        <Breadcrumbs />
        <ErrorState title="Source Not Found" message={`Could not find source with ID: ${sourceId}`} />
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
    { label: "Inventory Records", value: source.inventoryTotals.records, type: "NUMBER" as const },
    { label: "Last Metadata Refresh", value: source.lastMetadataRefresh, type: "DATETIME" as const },
  ];

  return (
    <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8 space-y-6">

      <PageHeader 
        title={source.name} 
        description={`Source ID: ${source.id}`}
        children={<EngineBadge engine={source.engine} />}
      />
      <FixtureNotice />

      {source.dependencyWarnings.length > 0 && (
        <div className="bg-orange-50 border-l-4 border-orange-400 p-4">
          <div className="flex">
            <div className="ml-3">
              <h3 className="text-sm font-medium text-orange-800">Warnings</h3>
              <div className="mt-2 text-sm text-orange-700">
                <ul className="list-disc pl-5 space-y-1">
                  {source.dependencyWarnings.map((warning, idx) => (
                    <li key={idx}>{warning}</li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        </div>
      )}

      <Tabs 
        activeTab={activeTab} 
        onChange={setActiveTab} 
        tabs={[
          { id: "summary", label: "Configuration Summary" },
          { id: "assets", label: "Assets" },
          { id: "governance", label: "Governance" },
          { id: "activity", label: "Activity" }
        ]} 
      />

      {activeTab === "summary" && (
        <div className="bg-white shadow overflow-hidden sm:rounded-lg">
          <PropertyList properties={summaryProps} />
        </div>
      )}

      {activeTab === "assets" && (
        <div className="p-8 text-center text-gray-500 bg-white shadow rounded-lg border border-gray-200">
          Assets integration pending Stage 3D
        </div>
      )}

      {activeTab === "governance" && (
        <div className="p-8 text-center text-gray-500 bg-white shadow rounded-lg border border-gray-200">
          Governance policies apply to all assets in this source.
        </div>
      )}

      {activeTab === "activity" && (
        <div className="p-8 text-center text-gray-500 bg-white shadow rounded-lg border border-gray-200">
          Recent activity timeline
        </div>
      )}
    </div>
  );
}


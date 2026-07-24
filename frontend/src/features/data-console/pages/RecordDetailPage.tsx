import { useParams } from "wouter";
import { PageHeader } from "../../../components/PageHeader";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { Breadcrumbs } from "../../../components/Breadcrumbs";
import { useRecordDetail, useBrowserAssets } from "../../../api/browserQueries";
import { FixtureNotice } from "../components/FixtureNotice";
import { PropertyList, type PropertyItem } from "../components/PropertyList";
import { JsonInspector } from "../components/JsonInspector";
import { type EngineType } from "../../../contracts/browser";

export function RecordDetailPage() {
  const params = useParams();
  const engine = decodeURIComponent(params.engine ?? "") as EngineType;
  const assetId = decodeURIComponent(params.assetId ?? "");
  const recordId = decodeURIComponent(params.recordId ?? "");

  const { data: record, isLoading, error } = useRecordDetail(engine, assetId, recordId);
  const { data: assets } = useBrowserAssets();
  const asset = assets?.find((a) => a.assetId === assetId && a.engine === engine);

  if (isLoading) return <LoadingState message="Loading record details..." />;
  if (error) {
    if (error instanceof Error && error.message.includes("CAPABILITY_ERROR")) {
      return (
        <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8">
          <Breadcrumbs />
          <ErrorState 
            title="Data Browser Unavailable" 
            message="The Data Browser capability requires Fixture Mode. Enable VITE_MOCK_MODE to view this page." 
          />
        </div>
      );
    }
    return (
      <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8">
        <Breadcrumbs />
        <ErrorState title="Failed to load Record" message={error instanceof Error ? error.message : "Unknown error"} />
      </div>
    );
  }

  if (!record) {
    return (
      <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8">
        <Breadcrumbs />
        <ErrorState title="Record Not Found" message={`Record ID ${recordId} was not found in ${assetId}.`} />
      </div>
    );
  }

  const mapRecordToProps = (): PropertyItem[] => {
    const props: PropertyItem[] = [];
    switch (record.kind) {
      case "SQL_ROW":
        Object.entries(record.fields).forEach(([key, meta]) => {
          props.push({
            label: key,
            value: record.data[key],
            type: meta.type,
            redacted: meta.redacted
          });
        });
        break;
      case "NEO4J_NODE":
        Object.entries(record.propertyTypes).forEach(([key, meta]) => {
          props.push({
            label: key,
            value: record.properties[key],
            type: meta.type,
            redacted: meta.redacted
          });
        });
        break;
    }
    return props;
  };

  return (
    <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8 space-y-6">

      <PageHeader 
        title={`Record: ${record.identity.id}`} 
        description={`Asset: ${asset?.name ?? assetId} | Engine: ${engine as string}`} 
      >
        <div className="mt-4 flex space-x-3 md:mt-0 md:ml-4">
          {asset?.capability === "WRITABLE" ? (
            <>
              <button type="button" className="inline-flex items-center px-4 py-2 border border-gray-300 rounded-md shadow-sm text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 focus:outline-none">
                Edit
              </button>
              <button type="button" className="inline-flex items-center px-4 py-2 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-red-600 hover:bg-red-700 focus:outline-none">
                Delete
              </button>
            </>
          ) : (
            <span className="inline-flex items-center px-3 py-2 text-sm text-gray-700 bg-gray-100 rounded-md" title="This record belongs to a read-only source system">
              Read Only Source - Mutations Disabled
            </span>
          )}
        </div>
      </PageHeader>
      <FixtureNotice />

      <div className="bg-white shadow sm:rounded-lg overflow-hidden">
        <div className="px-4 py-5 sm:px-6">
          <h2 className="text-lg leading-6 font-medium text-gray-900">Record Data</h2>
        </div>
        <div className="border-t border-gray-200">
          {record.kind === "MONGO_DOCUMENT" ? (
            <div className="p-4">
              <JsonInspector data={record.data} redactedPaths={record.redactedPaths} />
            </div>
          ) : (
            <PropertyList properties={mapRecordToProps()} />
          )}
        </div>
      </div>

      <div className="bg-white shadow sm:rounded-lg overflow-hidden">
        <div className="px-4 py-5 sm:px-6">
          <h2 className="text-lg leading-6 font-medium text-gray-900">Activity Timeline (Fixture)</h2>
        </div>
        <div className="border-t border-gray-200 p-8 text-center text-gray-500">
          Source records are read-only. Mutations must occur in an isolated workspace.
        </div>
      </div>
    </div>
  );
}


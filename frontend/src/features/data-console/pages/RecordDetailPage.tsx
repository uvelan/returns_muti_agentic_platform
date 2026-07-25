import { useParams } from "wouter";
import { PageHeader } from "../../../components/PageHeader";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { Breadcrumbs } from "../../../components/Breadcrumbs";
import { useBrowserAssets, useRecordDetail } from "../../../api/browserQueries";
import { PropertyList, type PropertyItem } from "../components/PropertyList";
import { JsonInspector } from "../components/JsonInspector";
import { type BrowserRecord, type EngineType } from "../../../contracts/browser";

function mapRecordToProps(record: BrowserRecord): PropertyItem[] {
  if (record.kind === "MONGO_DOCUMENT") return [];
  const metadata = record.kind === "SQL_ROW" ? record.fields : record.propertyTypes;
  const data = record.kind === "SQL_ROW" ? record.data : record.properties;
  return Object.entries(metadata).map(([key, meta]) => ({
    label: key,
    value: data[key],
    type: meta.type,
    redacted: meta.redacted,
  }));
}

export function RecordDetailPage() {
  const params = useParams();
  const engine = decodeURIComponent(params.engine ?? "") as EngineType;
  const assetId = decodeURIComponent(params.assetId ?? "");
  const recordId = decodeURIComponent(params.recordId ?? "");
  const { data: record, isLoading, error } = useRecordDetail(engine, assetId, recordId);
  const { data: assets } = useBrowserAssets();
  const asset = assets?.find((candidate) => candidate.assetId === assetId && candidate.engine === engine);

  if (isLoading) return <LoadingState message="Loading record details..." />;
  if (error || !record) {
    return (
      <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8">
        <Breadcrumbs />
        <ErrorState
          title="Record not found"
          message={error instanceof Error ? error.message : `Record ${recordId} was not found.`}
        />
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8 space-y-6">
      <PageHeader
        title={`Record: ${record.identity.id}`}
        description={`Asset: ${asset?.name ?? assetId} · Engine: ${engine}`}
      >
        <span className="rounded-md bg-gray-100 px-3 py-2 text-sm text-gray-700">
          Read-only · Sensitive fields redacted
        </span>
      </PageHeader>

      <div className="overflow-hidden bg-white shadow sm:rounded-lg">
        <div className="px-4 py-5 sm:px-6">
          <h2 className="text-lg font-medium text-gray-900">Record Data</h2>
        </div>
        <div className="border-t border-gray-200">
          {record.kind === "MONGO_DOCUMENT" ? (
            <div className="p-4">
              <JsonInspector data={record.data} redactedPaths={record.redactedPaths} />
            </div>
          ) : (
            <PropertyList properties={mapRecordToProps(record)} />
          )}
        </div>
      </div>

      <div className="overflow-hidden bg-white shadow sm:rounded-lg">
        <div className="px-4 py-5 sm:px-6">
          <h2 className="text-lg font-medium text-gray-900">Governance Evidence</h2>
        </div>
        <div className="border-t border-gray-200 p-6 text-sm text-gray-700 space-y-2">
          <p>The Data Browser performs no mutation operations.</p>
          <p>Values matching secret or personal-data field patterns are redacted by the API.</p>
          <p>Use AI Studio for governed proposals and sandbox-safe data generation.</p>
        </div>
      </div>
    </div>
  );
}

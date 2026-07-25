import { useMemo, useState } from "react";
import { useLocation, useParams } from "wouter";
import { PageHeader } from "../../../components/PageHeader";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { Breadcrumbs } from "../../../components/Breadcrumbs";
import { useBrowserAssets, useBrowserRecords } from "../../../api/browserQueries";
import { PaginationControls } from "../components/PaginationControls";
import { DetailDrawer } from "../components/DetailDrawer";
import { PropertyList, type PropertyItem } from "../components/PropertyList";
import { JsonInspector } from "../components/JsonInspector";
import { type BrowserRecord, type EngineType } from "../../../contracts/browser";
import { SearchInput } from "../components/SearchInput";

function recordData(record: BrowserRecord): Record<string, unknown> {
  if (record.kind === "MONGO_DOCUMENT" || record.kind === "SQL_ROW") return record.data;
  return record.properties;
}

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

export function AssetBrowserPage() {
  const params = useParams();
  const [, setLocation] = useLocation();
  const engine = decodeURIComponent(params.engine ?? "") as EngineType;
  const assetId = decodeURIComponent(params.assetId ?? "");
  const [pageIndex, setPageIndex] = useState(0);
  const [pageSize, setPageSize] = useState(10);
  const [search, setSearch] = useState("");
  const [selectedRecord, setSelectedRecord] = useState<BrowserRecord | null>(null);

  const { data: assets } = useBrowserAssets();
  const asset = assets?.find((candidate) => candidate.assetId === assetId && candidate.engine === engine);
  const { data: recordsData, isLoading, error } = useBrowserRecords(
    engine,
    assetId,
    pageIndex,
    pageSize,
  );

  const records = useMemo(() => {
    const page = recordsData?.data ?? [];
    if (!search.trim()) return page;
    const normalized = search.toLowerCase();
    return page.filter((record) =>
      JSON.stringify(recordData(record)).toLowerCase().includes(normalized),
    );
  }, [recordsData, search]);

  if (isLoading) return <LoadingState message="Loading records..." />;
  if (error) {
    return (
      <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8">
        <Breadcrumbs />
        <ErrorState
          title="Failed to load records"
          message={error instanceof Error ? error.message : "Unknown error"}
        />
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8 space-y-6">
      <PageHeader
        title={asset?.name ?? assetId}
        description={`Engine: ${engine} · Read-only governed inspection`}
      >
        <button
          type="button"
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          onClick={() => setLocation("/data-console/ai-studio")}
        >
          Open AI Studio
        </button>
      </PageHeader>

      <div className="bg-white shadow sm:rounded-lg">
        <div className="border-b border-gray-200 px-4 py-5">
          <SearchInput
            value={search}
            onChange={setSearch}
            placeholder="Search the currently loaded page..."
            className="w-full max-w-md"
          />
        </div>

        <div className="overflow-x-auto">
          {records.length === 0 ? (
            <div className="p-8 text-center text-gray-500">No records found.</div>
          ) : (
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium uppercase text-gray-500">ID</th>
                  <th className="px-6 py-3 text-left text-xs font-medium uppercase text-gray-500">
                    Data Summary
                  </th>
                  <th className="relative px-6 py-3"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 bg-white">
                {records.map((record) => (
                  <tr
                    key={record.identity.id}
                    className="cursor-pointer hover:bg-gray-50"
                    onClick={() => setSelectedRecord(record)}
                  >
                    <td className="px-6 py-4 text-sm font-medium text-gray-900">
                      {record.identity.id}
                    </td>
                    <td className="max-w-lg px-6 py-4 text-sm text-gray-500">
                      <div className="truncate font-mono">{JSON.stringify(recordData(record))}</div>
                    </td>
                    <td className="px-6 py-4 text-right text-sm font-medium">
                      <button
                        type="button"
                        className="text-blue-600 hover:text-blue-900"
                        onClick={(event) => {
                          event.stopPropagation();
                          setLocation(
                            `/data-console/browser/${encodeURIComponent(engine)}/${encodeURIComponent(assetId)}/records/${encodeURIComponent(record.identity.id)}`,
                          );
                        }}
                      >
                        Details
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <PaginationControls
          pageIndex={pageIndex}
          pageSize={pageSize}
          hasMore={recordsData?.page?.has_more ?? false}
          onPageChange={setPageIndex}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setPageIndex(0);
          }}
        />
      </div>

      <DetailDrawer
        isOpen={selectedRecord !== null}
        onClose={() => setSelectedRecord(null)}
        title={`Record Inspection: ${selectedRecord?.identity.id ?? ""}`}
      >
        {selectedRecord && (
          <div className="space-y-6">
            {selectedRecord.kind === "MONGO_DOCUMENT" ? (
              <JsonInspector
                data={selectedRecord.data}
                redactedPaths={selectedRecord.redactedPaths}
              />
            ) : (
              <PropertyList properties={mapRecordToProps(selectedRecord)} />
            )}
            <button
              type="button"
              className="w-full rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
              onClick={() =>
                setLocation(
                  `/data-console/browser/${encodeURIComponent(engine)}/${encodeURIComponent(assetId)}/records/${encodeURIComponent(selectedRecord.identity.id)}`,
                )
              }
            >
              View full details
            </button>
          </div>
        )}
      </DetailDrawer>
    </div>
  );
}

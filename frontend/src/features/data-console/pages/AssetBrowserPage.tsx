/* eslint-disable */
import { useState } from "react";
import { useParams, useLocation } from "wouter";
import { PageHeader } from "../../../components/PageHeader";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { Breadcrumbs } from "../../../components/Breadcrumbs";
import { useBrowserAssets, useBrowserRecords } from "../../../api/browserQueries";
import { FixtureNotice } from "../components/FixtureNotice";
import { PaginationControls } from "../components/PaginationControls";
import { DetailDrawer } from "../components/DetailDrawer";
import { PropertyList, type PropertyItem } from "../components/PropertyList";
import { JsonInspector } from "../components/JsonInspector";
import { type BrowserRecord, type EngineType } from "../../../contracts/browser";
import { SearchInput } from "../components/SearchInput";

export function AssetBrowserPage() {
  const params = useParams();
  const [, setLocation] = useLocation();
  const engine = decodeURIComponent(params.engine ?? "") as EngineType;
  const assetId = decodeURIComponent(params.assetId ?? "");

  const { data: assets } = useBrowserAssets();
  const asset = assets?.find((a) => a.assetId === assetId && a.engine === engine);
  const [pageIndex, setPageIndex] = useState(0);
  const [pageSize, setPageSize] = useState(10);
  const [search, setSearch] = useState("");

  const { data: recordsData, isLoading, error } = useBrowserRecords(engine, assetId, null, pageSize);

  const [selectedRecord, setSelectedRecord] = useState<BrowserRecord | null>(null);

  if (isLoading) return <LoadingState message="Loading records..." />;
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
        <ErrorState title="Failed to load Records" message={error instanceof Error ? error.message : "Unknown error"} />
      </div>
    );
  }

  const records = recordsData?.data ?? [];
  const hasMore = recordsData?.page?.has_more ?? false;

  const renderRecordCell = (record: BrowserRecord) => {
    switch (record.kind) {
      case "SQL_ROW":
        return <div className="text-sm font-mono truncate">{JSON.stringify(record.data)}</div>;
      case "MONGO_DOCUMENT":
        return <div className="text-sm font-mono truncate">{JSON.stringify(record.data)}</div>;
      case "NEO4J_NODE":
        return <div className="text-sm font-mono truncate">{JSON.stringify(record.properties)}</div>;
      case "NEO4J_RELATIONSHIP":
        return <div className="text-sm font-mono truncate">{JSON.stringify(record.properties)}</div>;
    }
  };

  const mapRecordToProps = (record: BrowserRecord): PropertyItem[] => {
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
      // MongoDB handled via JsonInspector primarily
    }
    return props;
  };

  return (
    <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8 space-y-6">

      <PageHeader 
        title={asset?.name ?? assetId} 
        description={`Engine: ${engine as string}`} 
      />
      <FixtureNotice />

      <div className="bg-white shadow sm:rounded-lg">
        <div className="px-4 py-5 flex flex-col sm:flex-row justify-between items-center space-y-4 sm:space-y-0 sm:space-x-4 border-b border-gray-200">
          <SearchInput value={search} onChange={setSearch} placeholder="Search loaded records..." className="w-full max-w-md" />
          <div className="flex space-x-2">
            {asset?.capability === "WRITABLE" ? (
              <button className="inline-flex items-center px-4 py-2 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 focus:outline-none">
                Add Record
              </button>
            ) : (
              <span className="inline-flex items-center px-3 py-2 text-sm text-gray-700 bg-gray-100 rounded-md" title="Source is read-only">
                Read Only
              </span>
            )}
          </div>
        </div>

        <div className="overflow-x-auto">
          {records.length === 0 ? (
            <div className="p-8 text-center text-gray-500">No records found.</div>
          ) : (
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">ID</th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Data Summary</th>
                  <th scope="col" className="relative px-6 py-3"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {records.map((r) => (
                  <tr key={r.identity.id} className="hover:bg-gray-50 cursor-pointer" onClick={() => { setSelectedRecord(r); }}>
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">{r.identity.id}</td>
                    <td className="px-6 py-4 text-sm text-gray-500 max-w-lg">{renderRecordCell(r)}</td>
                    <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                      <button 
                        className="text-blue-600 hover:text-blue-900"
                        onClick={(e) => {
                          e.stopPropagation();
                          setLocation(`/data-console/browser/${encodeURIComponent(engine as string)}/${encodeURIComponent(assetId)}/records/${encodeURIComponent(r.identity.id)}`);
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
          hasMore={hasMore} 
          onPageChange={setPageIndex} 
          onPageSizeChange={(size) => { setPageSize(size); setPageIndex(0); }} 
        />
      </div>

      <DetailDrawer 
        isOpen={!!selectedRecord} 
        onClose={() => { setSelectedRecord(null); }} 
        title={`Record Inspection: ${selectedRecord?.identity.id}`}
      >
        {selectedRecord && (
          <div className="space-y-6">
            <div>
              <h3 className="text-sm font-medium text-gray-900 mb-2">Record Properties</h3>
              {selectedRecord.kind === "MONGO_DOCUMENT" ? (
                <JsonInspector data={selectedRecord.data} redactedPaths={selectedRecord.redactedPaths} />
              ) : (
                <PropertyList properties={mapRecordToProps(selectedRecord)} />
              )}
            </div>
            <div className="pt-4 border-t border-gray-200">
              <button
                className="w-full flex justify-center py-2 px-4 border border-gray-300 rounded-md shadow-sm text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
                onClick={() => {
                  setLocation(`/data-console/browser/${encodeURIComponent(engine as string)}/${encodeURIComponent(assetId)}/records/${encodeURIComponent(selectedRecord.identity.id)}`);
                }}
              >
                View Full Details & Activity
              </button>
            </div>
          </div>
        )}
      </DetailDrawer>
    </div>
  );
}


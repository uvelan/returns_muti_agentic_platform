import { useState, useMemo } from "react";
import { useRoute, useLocation } from "wouter";
import { useWorkspaceRecord, useUpdateWorkspaceRecord } from "../../../../api/workspacesQueries";
import { PageHeader } from "../../../../components/PageHeader";
import { LoadingState } from "../../../../components/LoadingState";
import { ErrorState } from "../../../../components/ErrorState";

export function WorkspaceRecordEditPage() {
  const [, params] = useRoute("/data-console/workspaces/:workspaceId/records/:recordId/edit");
  const [, setLocation] = useLocation();
  const workspaceId = params?.workspaceId ?? "";
  const recordId = params?.recordId ?? "";

  const { data, isLoading, isError, error } = useWorkspaceRecord(workspaceId, recordId);
  const updateRecord = useUpdateWorkspaceRecord();

  // Derive initial JSON string from query data — no effect needed
  const initialJson = useMemo(
    () => (data ? JSON.stringify(data.data, null, 2) : ""),
    // Only recompute when the record ID changes, not on every re-render
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [recordId, data?.id]
  );

  const [formData, setFormData] = useState<string | null>(null);
  const [jsonError, setJsonError] = useState<string | null>(null);

  // Use derived value unless the user has edited
  const displayValue = formData ?? initialJson;

  if (isLoading) return <LoadingState message="Loading record..." />;
  if (isError || !data) return <ErrorState title="Failed to load record" message={error instanceof Error ? error.message : "Not found"} />;

  const handleSave = () => {
    try {
      const parsed = JSON.parse(displayValue) as Record<string, unknown>;
      setJsonError(null);
      updateRecord.mutate(
        { workspaceId, recordId, expectedVersion: data.version, payload: { data: parsed } },
        { onSuccess: () => { setLocation(`/data-console/workspaces/${workspaceId}`); } }
      );
    } catch (err) {
      if (err instanceof SyntaxError) {
        setJsonError("Invalid JSON format");
      }
    }
  };

  const issues = data.issues;

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <PageHeader title={`Edit Record: ${recordId}`} description="Modify sandbox record data. Changes are local to this workspace." />

      {data.validationStatus !== "VALID" && issues && issues.length > 0 && (
        <div className="bg-amber-50 border-l-4 border-amber-500 p-4 mb-6 rounded-r shadow-sm">
          <h4 className="text-sm font-bold text-amber-800">Validation Warnings</h4>
          <ul className="list-disc pl-5 mt-2 space-y-1 text-sm text-amber-700">
            {issues.map((issue, i) => (
              <li key={i}>{issue.message} {issue.field && <span className="font-mono bg-amber-100 px-1 text-xs">({issue.field})</span>}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="bg-white rounded border border-gray-200 shadow-sm flex flex-col h-[500px]">
        <div className="p-3 border-b border-gray-200 bg-gray-50 flex justify-between items-center">
          <span className="text-sm font-medium text-gray-700">Record Data (JSON)</span>
          {jsonError && <span className="text-sm text-red-600 font-medium">{jsonError}</span>}
        </div>
        <textarea
          className="flex-1 w-full p-4 font-mono text-sm resize-none focus:outline-none focus:ring-2 focus:ring-inset focus:ring-blue-500"
          value={displayValue}
          onChange={(e) => { setFormData(e.target.value); }}
          spellCheck={false}
        />
        <div className="p-4 border-t border-gray-200 flex justify-end space-x-3 bg-gray-50">
          <button type="button" onClick={() => { setLocation(`/data-console/workspaces/${workspaceId}`); }}
            className="px-4 py-2 text-sm text-gray-700 border border-gray-300 rounded hover:bg-gray-100 bg-white">Cancel</button>
          <button type="button" onClick={handleSave} disabled={updateRecord.isPending}
            className="px-4 py-2 text-sm text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50">
            {updateRecord.isPending ? "Saving..." : "Save Changes"}
          </button>
        </div>
      </div>
    </div>
  );
}

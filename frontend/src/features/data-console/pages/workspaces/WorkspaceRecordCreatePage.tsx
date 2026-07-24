import { useState } from "react";
import { useLocation, useRoute } from "wouter";
import { useCreateWorkspaceRecord } from "../../../../api/workspacesQueries";
import { PageHeader } from "../../../../components/PageHeader";

const DEFAULT_RECORD = JSON.stringify({ key: "value" }, null, 2);

export function WorkspaceRecordCreatePage() {
  const [, params] = useRoute("/data-console/workspaces/:workspaceId/records/new");
  const [, setLocation] = useLocation();
  const workspaceId = params?.workspaceId ?? "";
  const createRecord = useCreateWorkspaceRecord();
  const [json, setJson] = useState(DEFAULT_RECORD);
  const [jsonError, setJsonError] = useState<string | null>(null);

  const handleSubmit = () => {
    try {
      const parsed = JSON.parse(json) as unknown;
      if (parsed === null || Array.isArray(parsed) || typeof parsed !== "object") {
        setJsonError("Record data must be a JSON object.");
        return;
      }
      setJsonError(null);
      createRecord.mutate(
        {
          workspaceId,
          payload: {
            data: parsed as Record<string, unknown>,
            idempotencyKey: crypto.randomUUID(),
          },
        },
        { onSuccess: () => { setLocation(`/data-console/workspaces/${workspaceId}`); } },
      );
    } catch (error) {
      setJsonError(error instanceof SyntaxError ? "Invalid JSON format." : "Unable to parse record.");
    }
  };

  return (
    <div className="mx-auto max-w-4xl p-6">
      <PageHeader title="New Workspace Record" description="Create a durable, isolated record in Platform MongoDB." />
      <div className="flex h-[520px] flex-col overflow-hidden rounded border border-gray-200 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-gray-200 bg-gray-50 p-3">
          <span className="text-sm font-medium text-gray-700">Record data (JSON object)</span>
          {jsonError && <span className="text-sm font-medium text-red-600">{jsonError}</span>}
        </div>
        <textarea
          className="w-full flex-1 resize-none p-4 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-inset focus:ring-blue-500"
          value={json}
          onChange={(event) => { setJson(event.target.value); }}
          spellCheck={false}
        />
        <div className="flex justify-end gap-3 border-t border-gray-200 bg-gray-50 p-4">
          <button type="button" onClick={() => { setLocation(`/data-console/workspaces/${workspaceId}`); }} className="rounded border border-gray-300 bg-white px-4 py-2 text-sm text-gray-700 hover:bg-gray-100">Cancel</button>
          <button type="button" onClick={handleSubmit} disabled={createRecord.isPending} className="rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50">
            {createRecord.isPending ? "Creating..." : "Create record"}
          </button>
        </div>
      </div>
      {createRecord.isError && <p className="mt-3 text-sm text-red-600">{createRecord.error.message}</p>}
    </div>
  );
}

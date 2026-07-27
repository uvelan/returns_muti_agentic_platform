import { useState } from "react";
import { useLocation } from "wouter";
import { useCreateWorkspace } from "../../../../api/workspacesQueries";
import { PageHeader } from "../../../../components/PageHeader";

export function WorkspaceCreatePage() {
  const [, setLocation] = useLocation();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const createWorkspace = useCreateWorkspace();

  const handleSubmit = (e: React.SyntheticEvent) => {
    e.preventDefault();
    if (!name) return;
    createWorkspace.mutate(
      { name, description },
      { onSuccess: (ws) => { setLocation(`/data-console/workspaces/${ws.id}`); } }
    );
  };

  return (
    <div className="p-6 max-w-2xl mx-auto">
      <PageHeader title="Create Isolated Workspace" description="Initialize a durable Platform MongoDB workspace for isolated mutations." />

      <div className="bg-amber-50 border-l-4 border-amber-500 p-4 mb-6 text-sm text-amber-900 rounded-r shadow-sm">
        <p className="font-semibold uppercase tracking-wide">Workspace isolation</p>
        <p className="mt-1">Edits persist in the isolated workspace and do not affect source collections.</p>
      </div>

      <form onSubmit={handleSubmit} className="bg-white p-6 rounded border border-gray-200 space-y-4 shadow-sm">
        <div>
          <label htmlFor="ws-name" className="block text-sm font-medium text-gray-700 mb-1">Workspace Name</label>
          <input id="ws-name" type="text" required
            className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder="e.g. Sales Fixes Q4" value={name} onChange={(e) => { setName(e.target.value); }} />
        </div>
        <div>
          <label htmlFor="ws-desc" className="block text-sm font-medium text-gray-700 mb-1">Description</label>
          <textarea id="ws-desc"
            className="w-full border border-gray-300 rounded px-3 py-2 text-sm h-24 focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder="Purpose of this isolated workspace..." value={description} onChange={(e) => { setDescription(e.target.value); }} />
        </div>
        <div className="flex justify-end space-x-3 pt-4 border-t border-gray-100">
          <button type="button" onClick={() => { setLocation("/data-console/workspaces"); }}
            className="px-4 py-2 text-sm text-gray-700 border border-gray-300 rounded hover:bg-gray-50">Cancel</button>
          <button type="submit" disabled={createWorkspace.isPending || !name}
            className="px-4 py-2 text-sm text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50">
            {createWorkspace.isPending ? "Creating..." : "Create Workspace"}
          </button>
        </div>
      </form>
    </div>
  );
}

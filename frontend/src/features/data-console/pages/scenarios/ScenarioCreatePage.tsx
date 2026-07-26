import { useState } from "react";
import { useLocation } from "wouter";
import { useCreateScenario } from "../../../../api/scenariosQueries";
import { PageHeader } from "../../../../components/PageHeader";

export function ScenarioCreatePage() {
  const [, setLocation] = useLocation();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [baseWorkspaceId, setBaseWorkspaceId] = useState("");

  const createScenario = useCreateScenario();

  const handleSubmit = (e: React.SyntheticEvent) => {
    e.preventDefault();
    if (!name || !baseWorkspaceId) return;
    createScenario.mutate(
      { name, description, baseWorkspaceId, parameters: { param1: "value1" } },
      { onSuccess: (s) => { setLocation(`/data-console/scenarios/${s.id}`); } }
    );
  };

  return (
    <div className="p-6 max-w-2xl mx-auto">
      <PageHeader title="Generate What-If Scenario" description="Prompt the intelligence engine to project changes over a base workspace." />
      <form onSubmit={handleSubmit} className="bg-white p-6 rounded border border-gray-200 space-y-4 shadow-sm">
        <div>
          <label htmlFor="sc-name" className="block text-sm font-medium text-gray-700 mb-1">Scenario Name</label>
          <input id="sc-name" type="text" required
            className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-500"
            placeholder="e.g. Q4 Stress Test" value={name} onChange={(e) => { setName(e.target.value); }} />
        </div>
        <div>
          <label htmlFor="sc-desc" className="block text-sm font-medium text-gray-700 mb-1">Prompt / Description</label>
          <textarea id="sc-desc"
            className="w-full border border-gray-300 rounded px-3 py-2 text-sm h-24 focus:outline-none focus:ring-2 focus:ring-purple-500"
            placeholder="Describe the condition to project..." value={description} onChange={(e) => { setDescription(e.target.value); }} />
        </div>
        <div>
          <label htmlFor="sc-ws" className="block text-sm font-medium text-gray-700 mb-1">Base Workspace ID</label>
          <input id="sc-ws" type="text" required
            className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-500"
            placeholder="ws-sandbox-1" value={baseWorkspaceId} onChange={(e) => { setBaseWorkspaceId(e.target.value); }} />
        </div>
        <div className="flex justify-end space-x-3 pt-4 border-t border-gray-100">
          <button type="button" onClick={() => { setLocation("/data-console/scenarios"); }}
            className="px-4 py-2 text-sm text-gray-700 border border-gray-300 rounded hover:bg-gray-50">Cancel</button>
          <button type="submit" disabled={createScenario.isPending || !name || !baseWorkspaceId}
            className="px-4 py-2 text-sm text-white bg-purple-600 rounded hover:bg-purple-700 disabled:opacity-50">
            {createScenario.isPending ? "Generating..." : "Generate Scenario"}
          </button>
        </div>
      </form>
    </div>
  );
}

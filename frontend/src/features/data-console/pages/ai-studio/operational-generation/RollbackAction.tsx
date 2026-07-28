import { useState } from "react";
import {
  rollbackRun,
  type ExecutionRun,
} from "../../../../../api/operationalGeneration";

export function RollbackAction() {
  const [runId, setRunId] = useState("");
  const [planId, setPlanId] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ExecutionRun | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleRollback = async () => {
    if (!runId.trim() || !planId.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await rollbackRun(runId, planId);
      setResult(res);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Rollback failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-4 border rounded bg-gray-50 max-w-lg">
      <div className="flex flex-col space-y-2 mb-4">
        <input
          type="text"
          value={runId}
          onChange={(e) => { setRunId(e.target.value); }}
          placeholder="Enter Run ID to rollback"
          className="px-3 py-2 border rounded"
        />
        <input
          type="text"
          value={planId}
          onChange={(e) => { setPlanId(e.target.value); }}
          placeholder="Enter Plan ID"
          className="px-3 py-2 border rounded"
        />
        <button
          onClick={() => { void handleRollback(); }}
          disabled={loading || !runId.trim() || !planId.trim()}
          className="px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50"
        >
          {loading ? "Rolling back..." : "Rollback"}
        </button>
      </div>

      {error && (
        <div className="p-3 bg-red-100 text-red-700 rounded mb-2">{error}</div>
      )}

      {result && (
        <div className="p-3 bg-green-50 border border-green-200 rounded">
          <p className="font-semibold text-green-800">
            Rollback Status: {result.state}
          </p>
          <p className="text-sm text-green-700">Run ID: {result.run_id}</p>
        </div>
      )}
    </div>
  );
}

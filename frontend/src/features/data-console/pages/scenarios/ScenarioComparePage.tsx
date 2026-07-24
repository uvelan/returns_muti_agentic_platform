import { useRoute } from "wouter";
import type { ScenarioDiff } from "../../../../contracts/scenarios";
import { useScenarioDetail, useScenarioDiffs } from "../../../../api/scenariosQueries";
import { PageHeader } from "../../../../components/PageHeader";
import { LoadingState } from "../../../../components/LoadingState";
import { ErrorState } from "../../../../components/ErrorState";

export function ScenarioComparePage() {
  const [, params] = useRoute("/data-console/scenarios/:scenarioId/compare");
  const scenarioId = params?.scenarioId ?? "";

  const scenarioQuery = useScenarioDetail(scenarioId);
  const diffsQuery = useScenarioDiffs(scenarioId);

  if (scenarioQuery.isLoading || diffsQuery.isLoading) return <LoadingState message="Loading diff..." />;
  if (scenarioQuery.isError || !scenarioQuery.data) return <ErrorState title="Failed to load scenario" message={scenarioQuery.error instanceof Error ? scenarioQuery.error.message : "Unknown"} />;
  if (diffsQuery.isError) return <ErrorState title="Failed to load diffs" message={diffsQuery.error instanceof Error ? diffsQuery.error.message : "Unknown"} />;

  const scenario = scenarioQuery.data;
  const diffs: ScenarioDiff[] = diffsQuery.data ?? [];

  return (
    <div className="p-6">
      <PageHeader
        title={`Comparison: ${scenario.name}`}
        description={`Viewing projected changes against base workspace ${scenario.baseWorkspaceId}`}
      />

      <div className="bg-white rounded border border-gray-200 shadow-sm overflow-hidden">
        <table className="min-w-full text-left text-sm border-collapse">
          <thead>
            <tr className="bg-gray-50 border-b border-gray-200 text-gray-600">
              <th className="p-3 font-medium">Record ID</th>
              <th className="p-3 font-medium">Status</th>
              <th className="p-3 font-medium">Base Data</th>
              <th className="p-3 font-medium">Scenario Data</th>
              <th className="p-3 font-medium">AI Insights</th>
            </tr>
          </thead>
          <tbody>
            {diffs.map((diff, i) => (
              <tr key={i} className="border-b border-gray-100 align-top">
                <td className="p-3 font-mono text-xs">{diff.recordId}</td>
                <td className="p-3">
                  <span className={`px-2 py-1 rounded text-xs font-semibold ${diff.status === "ADDED" ? "bg-green-100 text-green-800" : diff.status === "REMOVED" ? "bg-red-100 text-red-800" : "bg-blue-100 text-blue-800"}`}>
                    {diff.status}
                  </span>
                </td>
                <td className="p-3 text-xs text-gray-600">
                  <pre>{JSON.stringify(diff.baseData, null, 2)}</pre>
                </td>
                <td className="p-3 text-xs text-green-700 font-medium">
                  <pre>{JSON.stringify(diff.scenarioData, null, 2)}</pre>
                </td>
                <td className="p-3 text-xs">
                  {diff.issues && diff.issues.length > 0 && (
                    <ul className="list-disc pl-4 text-purple-700">
                      {diff.issues.map((issue, idx) => <li key={idx}>{issue}</li>)}
                    </ul>
                  )}
                </td>
              </tr>
            ))}
            {diffs.length === 0 && (
              <tr>
                <td colSpan={5} className="p-8 text-center text-gray-500">No projected changes found.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

import { useRoute } from "wouter";
import { useScenarioDetail, useScenarioPreview } from "../../../../api/scenariosQueries";
import { ErrorState } from "../../../../components/ErrorState";
import { LoadingState } from "../../../../components/LoadingState";
import { PageHeader } from "../../../../components/PageHeader";

export function ScenarioPreviewPage() {
  const [, params] = useRoute("/data-console/scenarios/:scenarioId/preview");
  const scenarioId = params?.scenarioId ?? "";
  const scenario = useScenarioDetail(scenarioId);
  const preview = useScenarioPreview(scenarioId);
  if (scenario.isLoading || preview.isLoading) return <LoadingState message="Loading scenario preview..." />;
  if (scenario.isError || !scenario.data) return <ErrorState title="Scenario unavailable" message={scenario.error instanceof Error ? scenario.error.message : "Not found"} />;
  if (preview.isError) return <ErrorState title="Preview unavailable" message={preview.error instanceof Error ? preview.error.message : "Unknown error"} />;

  return (
    <div className="p-6">
      <PageHeader title={`Preview: ${scenario.data.name}`} description="Generated records tied to the current scenario digest." />
      <div className="space-y-3">
        {(preview.data ?? []).map((record) => <article key={record.recordId} className="rounded border border-gray-200 bg-white p-4"><div className="flex justify-between gap-3"><code className="text-xs">{record.recordId}</code><span className="text-xs text-gray-500">{record.issues.length} issues</span></div><pre className="mt-3 overflow-auto rounded bg-gray-950 p-4 text-xs text-gray-100">{JSON.stringify(record.data, null, 2)}</pre>{record.issues.length > 0 && <ul className="mt-3 list-disc pl-5 text-sm text-red-700">{record.issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>}</article>)}
        {(preview.data ?? []).length === 0 && <div className="rounded border border-dashed border-gray-300 p-8 text-center text-sm text-gray-500">Generate the scenario to materialize preview records.</div>}
      </div>
    </div>
  );
}

import { Link, useRoute } from "wouter";
import { useApproveScenario, useGenerateScenario, useScenarioDetail, useValidateScenario } from "../../../../api/scenariosQueries";
import { ErrorState } from "../../../../components/ErrorState";
import { LoadingState } from "../../../../components/LoadingState";
import { PageHeader } from "../../../../components/PageHeader";
import { PropertyList } from "../../components/PropertyList";

const STATUS_STYLE: Record<string, string> = {
  DRAFT: "bg-slate-100 text-slate-700",
  READY: "bg-green-100 text-green-800",
  APPROVED: "bg-blue-100 text-blue-800",
  FAILED: "bg-red-100 text-red-800",
  ARCHIVED: "bg-gray-100 text-gray-700",
};

export function ScenarioDetailPage() {
  const [, params] = useRoute("/data-console/scenarios/:scenarioId");
  const scenarioId = params?.scenarioId ?? "";
  const query = useScenarioDetail(scenarioId);
  const generate = useGenerateScenario();
  const validate = useValidateScenario();
  const approve = useApproveScenario();

  if (query.isLoading) return <LoadingState message="Loading scenario..." />;
  if (query.isError || !query.data) return <ErrorState title="Failed to load scenario" message={query.error instanceof Error ? query.error.message : "Not found"} />;

  const scenario = query.data;
  const validated = Boolean(scenario.generatedDigest && scenario.validatedDigest === scenario.generatedDigest && scenario.validationIssues.length === 0);
  const mutationError = generate.error ?? validate.error ?? approve.error;

  return (
    <div className="max-w-5xl p-6">
      <PageHeader title={scenario.name} description="Deterministic scenario projection and approval evidence.">
        <span className={`rounded px-2 py-1 text-xs font-semibold ${STATUS_STYLE[scenario.status] ?? STATUS_STYLE.DRAFT}`}>{scenario.status}</span>
      </PageHeader>

      <div className="mb-6 flex flex-wrap gap-3">
        <button type="button" onClick={() => { generate.mutate(scenario.id); }} disabled={generate.isPending || scenario.status === "APPROVED"} className="rounded bg-slate-900 px-4 py-2 text-sm text-white disabled:opacity-50">{generate.isPending ? "Generating..." : "Generate"}</button>
        <button type="button" onClick={() => { validate.mutate(scenario.id); }} disabled={validate.isPending || !scenario.generatedDigest || scenario.status === "APPROVED"} className="rounded bg-purple-600 px-4 py-2 text-sm text-white disabled:opacity-50">{validate.isPending ? "Validating..." : "Validate digest"}</button>
        <button type="button" onClick={() => { approve.mutate(scenario.id); }} disabled={approve.isPending || !validated || scenario.status === "APPROVED"} className="rounded bg-green-600 px-4 py-2 text-sm text-white disabled:opacity-50">{approve.isPending ? "Approving..." : "Approve"}</button>
        <Link href={`/data-console/scenarios/${scenario.id}/preview`} className="rounded border border-gray-300 px-4 py-2 text-sm hover:bg-gray-50">Preview</Link>
        <Link href={`/data-console/scenarios/${scenario.id}/compare`} className="rounded border border-gray-300 px-4 py-2 text-sm hover:bg-gray-50">Compare</Link>
      </div>
      {mutationError && <p className="mb-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">{mutationError.message}</p>}

      <div className="rounded border border-gray-200 bg-white p-4">
        <h3 className="mb-4 text-lg font-medium">Metadata</h3>
        <PropertyList properties={[
          { label: "Base workspace", value: scenario.baseWorkspaceId },
          { label: "Description", value: scenario.description },
          { label: "Owner", value: scenario.owner },
          { label: "Created", value: new Date(scenario.createdAt).toLocaleString() },
          { label: "Version", value: scenario.version },
          { label: "Generated digest", value: scenario.generatedDigest ?? "Not generated" },
          { label: "Validated digest", value: scenario.validatedDigest ?? "Not validated" },
          { label: "Parameters", value: JSON.stringify(scenario.parameters) },
        ]} />
      </div>
      {scenario.validationIssues.length > 0 && <section className="mt-6 rounded border border-red-200 bg-red-50 p-4 text-red-900"><h2 className="font-semibold">Validation issues</h2><ul className="mt-2 list-disc pl-5 text-sm">{scenario.validationIssues.map((issue) => <li key={issue}>{issue}</li>)}</ul></section>}
    </div>
  );
}

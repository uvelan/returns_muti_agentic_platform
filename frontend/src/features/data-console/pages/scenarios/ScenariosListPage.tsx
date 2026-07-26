import { Link, useLocation } from "wouter";
import type { Scenario } from "../../../../contracts/scenarios";
import { useScenariosList, useDeleteScenario } from "../../../../api/scenariosQueries";
import { PageHeader } from "../../../../components/PageHeader";
import { LoadingState } from "../../../../components/LoadingState";
import { ErrorState } from "../../../../components/ErrorState";
import { DataTable } from "../../components/DataTable";

const SCENARIO_STATUS_STYLE: Record<string, string> = {
  READY: "bg-green-100 text-green-800",
  FAILED: "bg-red-100 text-red-800",
  GENERATING: "bg-purple-100 text-purple-800",
  ARCHIVED: "bg-gray-100 text-gray-700",
};

export function ScenariosListPage() {
  const [, setLocation] = useLocation();
  const { data, isLoading, isError, error } = useScenariosList();
  const deleteScenario = useDeleteScenario();

  if (isLoading) return <LoadingState message="Loading scenarios..." />;
  if (isError) return <ErrorState title="Failed to load scenarios" message={error instanceof Error ? error.message : "Unknown error"} />;

  const scenarios = data ?? [];

  const handleDelete = (id: string) => {
    if (window.confirm("Are you sure you want to delete this scenario?")) {
      deleteScenario.mutate(id);
    }
  };

  return (
    <div className="p-6">
      <PageHeader title="Scenarios (What-If)" description="Generate, manage, and compare what-if projections using the intelligence engine.">
        <button onClick={() => { setLocation("/data-console/scenarios/new"); }}
          className="bg-blue-600 text-white px-4 py-2 rounded shadow text-sm font-medium hover:bg-blue-700">
          New Scenario
        </button>
      </PageHeader>

      <div className="bg-purple-50 border-l-4 border-purple-500 p-4 mb-6 text-sm text-purple-900 rounded-r shadow-sm">
        <p className="font-semibold uppercase tracking-wide">AI Generation Feature</p>
        <p className="mt-1">Scenarios deterministically project workspace records, validate outputs, and preserve generated digests.</p>
      </div>

      <DataTable<Scenario>
        keyExtractor={(row) => row.id}
        columns={[
          { header: "Name", accessor: (row) => <Link href={`/data-console/scenarios/${row.id}`} className="text-blue-600 hover:underline font-medium">{row.name}</Link> },
          { header: "Status", accessor: (row) => <span className={`px-2 py-1 rounded text-xs font-semibold ${SCENARIO_STATUS_STYLE[row.status] ?? "bg-gray-100 text-gray-700"}`}>{row.status}</span> },
          { header: "Base Workspace", accessor: (row) => <Link href={`/data-console/workspaces/${row.baseWorkspaceId}`} className="text-blue-600 hover:underline">{row.baseWorkspaceId}</Link> },
          { header: "Created At", accessor: (row) => new Date(row.createdAt).toLocaleString() },
          { header: "Owner", accessor: (row) => row.owner },
          { header: "Actions", accessor: (row) => (
            <div className="flex space-x-2">
              <Link href={`/data-console/scenarios/${row.id}/compare`} className="text-purple-600 hover:text-purple-800 text-sm">Compare</Link>
              <button onClick={() => { handleDelete(row.id); }} disabled={deleteScenario.isPending}
                className="text-red-600 hover:text-red-800 text-sm disabled:opacity-50">Delete</button>
            </div>
          )}
        ]}
        data={scenarios}
      />
    </div>
  );
}

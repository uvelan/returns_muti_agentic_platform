import { Link, useLocation } from "wouter";
import type { Workspace } from "../../../../contracts/workspaces";
import { useWorkspacesList, useDeleteWorkspace } from "../../../../api/workspacesQueries";
import { PageHeader } from "../../../../components/PageHeader";
import { LoadingState } from "../../../../components/LoadingState";
import { ErrorState } from "../../../../components/ErrorState";
import { DataTable } from "../../components/DataTable";

export function WorkspacesListPage() {
  const [, setLocation] = useLocation();
  const { data, isLoading, isError, error } = useWorkspacesList();
  const deleteWorkspace = useDeleteWorkspace();

  if (isLoading) return <LoadingState message="Loading workspaces..." />;
  if (isError) return <ErrorState title="Failed to load workspaces" message={error instanceof Error ? error.message : "Unknown error"} />;

  const workspaces = data ?? [];

  const handleDelete = (id: string, expectedVersion: number) => {
    if (window.confirm("Are you sure you want to delete this sandbox workspace?")) {
      deleteWorkspace.mutate({ workspaceId: id, expectedVersion });
    }
  };

  return (
    <div className="p-6">
      <PageHeader title="Workspaces" description="Manage temporary sandbox workspaces for safe data modeling.">
        <button onClick={() => { setLocation("/data-console/workspaces/new"); }}
          className="bg-blue-600 text-white px-4 py-2 rounded shadow text-sm font-medium hover:bg-blue-700">
          New Workspace
        </button>
      </PageHeader>

      <div className="bg-amber-50 border-l-4 border-amber-500 p-4 mb-6 text-sm text-amber-900 rounded-r shadow-sm">
        <p className="font-semibold uppercase tracking-wide">Local Sandbox Area</p>
        <p className="mt-1">Workspaces are durable Platform MongoDB sandboxes and never mutate source data.</p>
      </div>

      <DataTable<Workspace>
        keyExtractor={(row) => row.id}
        columns={[
          { header: "Name", accessor: (row) => <Link href={`/data-console/workspaces/${row.id}`} className="text-blue-600 hover:underline font-medium">{row.name}</Link> },
          { header: "Type", accessor: (row) => <span className="text-xs font-semibold bg-gray-100 px-2 py-1 rounded">{row.isSandbox ? "SANDBOX" : "STANDARD"}</span> },
          { header: "Records", accessor: (row) => String(row.recordCount) },
          { header: "Created At", accessor: (row) => new Date(row.createdAt).toLocaleString() },
          { header: "Owner", accessor: (row) => row.owner },
          { header: "Actions", accessor: (row) => (
            <button onClick={() => { handleDelete(row.id, row.version); }} disabled={deleteWorkspace.isPending}
              className="text-red-600 hover:text-red-800 text-sm disabled:opacity-50">
              Delete
            </button>
          )}
        ]}
        data={workspaces}
      />
    </div>
  );
}

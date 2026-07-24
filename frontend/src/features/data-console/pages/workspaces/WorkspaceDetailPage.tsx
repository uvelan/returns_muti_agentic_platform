import { FilePlus2, Trash2 } from "lucide-react";
import { Link, useRoute } from "wouter";
import { useDeleteWorkspaceRecord, useWorkspaceDetail, useWorkspaceRecords } from "../../../../api/workspacesQueries";
import { EmptyState } from "../../../../components/EmptyState";
import { ErrorState } from "../../../../components/ErrorState";
import { LoadingState } from "../../../../components/LoadingState";
import { PageHeader } from "../../../../components/PageHeader";
import { PropertyList } from "../../components/PropertyList";

export function WorkspaceDetailPage() {
  const [, params] = useRoute("/data-console/workspaces/:workspaceId");
  const workspaceId = params?.workspaceId ?? "";
  const workspaceQuery = useWorkspaceDetail(workspaceId);
  const recordsQuery = useWorkspaceRecords(workspaceId);
  const deleteRecord = useDeleteWorkspaceRecord();

  if (workspaceQuery.isLoading || recordsQuery.isLoading) return <LoadingState message="Loading workspace..." />;
  if (workspaceQuery.isError || !workspaceQuery.data) {
    return <ErrorState title="Failed to load workspace" message={workspaceQuery.error instanceof Error ? workspaceQuery.error.message : "Not found"} />;
  }
  if (recordsQuery.isError) {
    return <ErrorState title="Failed to load records" message={recordsQuery.error instanceof Error ? recordsQuery.error.message : "Unknown error"} />;
  }

  const workspace = workspaceQuery.data;
  const records = recordsQuery.data ?? [];

  const handleDelete = (recordId: string, expectedVersion: number) => {
    if (!window.confirm("Delete this sandbox record? This operation is audited.")) return;
    deleteRecord.mutate({ workspaceId, recordId, expectedVersion });
  };

  return (
    <div className="p-6">
      <PageHeader title={workspace.name} description={workspace.description}>
        <Link
          href={`/data-console/workspaces/${workspace.id}/records/new`}
          className="inline-flex items-center gap-2 rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
        >
          <FilePlus2 size={16} /> Add record
        </Link>
      </PageHeader>

      <div className="mb-6 rounded-r border-l-4 border-amber-500 bg-amber-50 p-4 text-sm text-amber-900 shadow-sm">
        <p className="font-semibold uppercase tracking-wide">Durable sandbox isolation</p>
        <p className="mt-1">Records persist in Platform MongoDB and never mutate authoritative source systems.</p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <div className="rounded border border-gray-200 bg-white p-4">
            <h3 className="mb-4 text-lg font-medium">Records</h3>
            {records.length === 0 ? (
              <EmptyState
                title="No records"
                description="Create the first isolated record in this workspace."
                action={<Link href={`/data-console/workspaces/${workspace.id}/records/new`} className="text-sm font-medium text-blue-600 hover:underline">Create record</Link>}
              />
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full border-collapse text-left text-sm">
                  <thead>
                    <tr className="border-b border-gray-200 bg-gray-50 text-gray-600">
                      <th className="p-2 font-medium">Record ID</th>
                      <th className="p-2 font-medium">Validation</th>
                      <th className="p-2 font-medium">Version</th>
                      <th className="p-2 font-medium">Updated</th>
                      <th className="p-2 font-medium">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {records.map((record) => (
                      <tr key={record.id} className="border-b border-gray-100">
                        <td className="p-2 font-mono text-xs">{record.id}</td>
                        <td className="p-2">{record.validationStatus}</td>
                        <td className="p-2">{record.version}</td>
                        <td className="p-2">{new Date(record.updatedAt).toLocaleString()}</td>
                        <td className="p-2">
                          <div className="flex items-center gap-3">
                            <Link href={`/data-console/workspaces/${workspace.id}/records/${record.id}/edit`} className="text-blue-600 hover:underline">Edit</Link>
                            <button
                              type="button"
                              onClick={() => { handleDelete(record.id, record.version); }}
                              disabled={deleteRecord.isPending}
                              className="inline-flex items-center gap-1 text-red-600 hover:text-red-800 disabled:opacity-50"
                            >
                              <Trash2 size={14} /> Delete
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>

        <div className="rounded border border-gray-200 bg-white p-4">
          <h3 className="mb-4 text-lg font-medium">Metadata</h3>
          <PropertyList properties={[
            { label: "ID", value: workspace.id },
            { label: "Type", value: workspace.isSandbox ? "Sandbox" : "Standard" },
            { label: "Owner", value: workspace.owner },
            { label: "Created", value: new Date(workspace.createdAt).toLocaleString() },
            { label: "Updated", value: new Date(workspace.updatedAt).toLocaleString() },
            { label: "Records", value: workspace.recordCount },
            { label: "Version", value: workspace.version },
          ]} />
        </div>
      </div>
    </div>
  );
}

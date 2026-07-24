import { Link, useLocation } from "wouter";
import type { Job } from "../../../../contracts/jobs";
import { useJobsList } from "../../../../api/jobsQueries";
import { PageHeader } from "../../../../components/PageHeader";
import { LoadingState } from "../../../../components/LoadingState";
import { ErrorState } from "../../../../components/ErrorState";
import { DataTable } from "../../components/DataTable";

const STATUS_STYLE: Record<string, string> = {
  COMPLETED: "bg-green-100 text-green-800",
  FAILED: "bg-red-100 text-red-800",
  RUNNING: "bg-amber-100 text-amber-800",
  PENDING: "bg-blue-100 text-blue-800",
};

export function ExportsListPage() {
  const [, setLocation] = useLocation();
  const { data, isLoading, isError, error } = useJobsList({ type: "EXPORT" });

  if (isLoading) return <LoadingState message="Loading exports..." />;
  if (isError) return <ErrorState title="Failed to load exports" message={error instanceof Error ? error.message : "Unknown error"} />;

  const jobs = data ?? [];

  return (
    <div className="p-6">
      <PageHeader title="Exports" description="History of all data exports.">
        <button
          onClick={() => { setLocation("/data-console/exports/new"); }}
          className="bg-blue-600 text-white px-4 py-2 rounded shadow text-sm font-medium hover:bg-blue-700"
        >
          New Export
        </button>
      </PageHeader>
      <DataTable<Job>
        keyExtractor={(row) => row.id}
        columns={[
          { header: "Job ID", accessor: (row) => <Link href={`/data-console/jobs/${row.id}`} className="text-blue-600 hover:underline">{row.id}</Link> },
          { header: "Status", accessor: (row) => <span className={`px-2 py-1 rounded text-xs font-semibold ${STATUS_STYLE[row.status] ?? "bg-gray-100 text-gray-700"}`}>{row.status}</span> },
          { header: "Source", accessor: (row) => row.target },
          { header: "Started At", accessor: (row) => row.startedAt ? new Date(row.startedAt).toLocaleString() : "-" },
          { header: "Owner", accessor: (row) => row.owner }
        ]}
        data={jobs}
      />
    </div>
  );
}

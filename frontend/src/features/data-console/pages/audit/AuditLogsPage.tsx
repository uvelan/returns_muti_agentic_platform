import { useMemo, useState } from "react";
import { useAuditLogs } from "../../../../api/consoleGovernanceQueries";
import type { AuditLog } from "../../../../contracts/consoleGovernance";
import { ErrorState } from "../../../../components/ErrorState";
import { LoadingState } from "../../../../components/LoadingState";
import { PageHeader } from "../../../../components/PageHeader";
import { DataTable } from "../../components/DataTable";

export function AuditLogsPage() {
  const [filter, setFilter] = useState("");
  const { data = [], isLoading, isError, error, refetch } = useAuditLogs();
  const filtered = useMemo(() => {
    const term = filter.trim().toLowerCase();
    if (!term) return data;
    return data.filter((log) => [log.action, log.actor, log.target, JSON.stringify(log.details)].some((value) => value.toLowerCase().includes(term)));
  }, [data, filter]);

  if (isLoading) return <LoadingState message="Loading audit evidence..." />;
  if (isError) return <ErrorState title="Failed to load audit evidence" message={error instanceof Error ? error.message : "Unknown error"} />;

  return (
    <div className="p-6">
      <PageHeader title="Audit Evidence" description="Immutable operational and data-console actions stored in Platform MongoDB.">
        <button type="button" onClick={() => void refetch()} className="rounded border border-gray-300 px-3 py-2 text-sm hover:bg-gray-50">Refresh</button>
      </PageHeader>
      <input
        value={filter}
        onChange={(event) => { setFilter(event.target.value); }}
        placeholder="Filter by action, actor, target, or details"
        className="mb-4 w-full max-w-xl rounded border border-gray-300 px-3 py-2 text-sm"
      />
      <DataTable<AuditLog>
        keyExtractor={(row) => row.id}
        columns={[
          { header: "Timestamp", accessor: (row) => new Date(row.timestamp).toLocaleString() },
          { header: "Actor", accessor: (row) => row.actor },
          { header: "Action", accessor: (row) => row.action },
          { header: "Target", accessor: (row) => <span className="font-mono text-xs">{row.target}</span> },
          { header: "Evidence", accessor: (row) => <code className="block max-w-md truncate text-xs">{JSON.stringify(row.details)}</code> },
        ]}
        data={filtered}
      />
    </div>
  );
}

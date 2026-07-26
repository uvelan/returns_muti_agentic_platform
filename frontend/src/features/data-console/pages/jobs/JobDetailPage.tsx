/* eslint-disable @typescript-eslint/no-unnecessary-condition, @typescript-eslint/restrict-template-expressions */
import { useParams } from "wouter";
import { useCancelJob, useJobDetail, useRetryJob } from "../../../../api/jobsQueries";
import { ErrorState } from "../../../../components/ErrorState";
import { LoadingState } from "../../../../components/LoadingState";
import { PageHeader } from "../../../../components/PageHeader";
import { PropertyList } from "../../components/PropertyList";

const STATUS_STYLE: Record<string, string> = {
  COMPLETED: "bg-green-100 text-green-800",
  FAILED: "bg-red-100 text-red-800",
  RUNNING: "bg-amber-100 text-amber-800",
  PENDING: "bg-blue-100 text-blue-800",
  CANCELLED: "bg-gray-200 text-gray-800",
};

export function JobDetailPage() {
  const params = useParams<{ jobId: string }>();
  const jobId = params.jobId ?? "";
  const query = useJobDetail(jobId);
  const cancel = useCancelJob(jobId);
  const retry = useRetryJob(jobId);

  if (query.isLoading) return <LoadingState message="Loading job details..." />;
  if (query.isError || !query.data) {
    return <ErrorState title="Failed to load job" message={query.error instanceof Error ? query.error.message : "Not found"} />;
  }

  const job = query.data;
  const actionError = cancel.error ?? retry.error;
  const canCancel = job.status === "PENDING" || job.status === "RUNNING";
  const canRetry = (job.status === "FAILED" || job.status === "CANCELLED") && job.attempts < job.maxAttempts;

  return (
    <div className="p-6 max-w-4xl">
      <PageHeader title={`Job: ${job.id}`} description={`Durable ${job.type.toLowerCase()} execution state and evidence.`}>
        <span className={`px-2 py-1 rounded text-xs font-semibold ${STATUS_STYLE[job.status] ?? "bg-gray-100 text-gray-700"}`}>{job.status}</span>
      </PageHeader>

      {actionError && <div className="mb-4"><ErrorState message={actionError.message} /></div>}

      <div className="mb-6 flex flex-wrap gap-3">
        {canCancel && (
          <button
            className="rounded bg-red-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            disabled={cancel.isPending}
            onClick={() => { cancel.mutate(); }}
            type="button"
          >
            {cancel.isPending ? "Requesting cancellation..." : "Cancel job"}
          </button>
        )}
        {canRetry && (
          <button
            className="rounded bg-blue-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            disabled={retry.isPending}
            onClick={() => { retry.mutate(); }}
            type="button"
          >
            {retry.isPending ? "Queueing retry..." : "Retry job"}
          </button>
        )}
        {job.type === "EXPORT" && job.status === "COMPLETED" && (
          <a
            className="rounded bg-green-700 px-4 py-2 text-sm font-medium text-white"
            href={`/data-console/v1/exports/${encodeURIComponent(job.id)}/download`}
          >
            Download artifact
          </a>
        )}
      </div>

      <div className="bg-white rounded border border-gray-200 p-4 mb-6">
        <h3 className="text-lg font-medium mb-4">Job Details</h3>
        <PropertyList properties={[
          { label: "Type", value: job.type },
          { label: "Target/Source", value: job.target },
          { label: "Owner", value: job.owner },
          { label: "Created At", value: new Date(job.createdAt).toLocaleString() },
          { label: "Started At", value: job.startedAt ? new Date(job.startedAt).toLocaleString() : "-" },
          { label: "Completed At", value: job.completedAt ? new Date(job.completedAt).toLocaleString() : "-" },
          { label: "Attempts", value: `${job.attempts}/${job.maxAttempts}` },
          { label: "Cancellation requested", value: job.cancellationRequestedAt ? new Date(job.cancellationRequestedAt).toLocaleString() : "No" },
        ]} />
      </div>

      <div className="bg-white rounded border border-gray-200 p-4 mb-6">
        <h3 className="text-lg font-medium mb-4">Execution Metrics</h3>
        <PropertyList properties={[
          { label: "Progress", value: `${String(job.metrics.progressPercentage ?? 0)}%` },
          { label: "Total Records", value: job.metrics.totalRecords ?? "-" },
          { label: "Processed Records", value: job.metrics.processedRecords ?? "-" },
          { label: "Failed Records", value: job.metrics.failedRecords ?? "-" },
        ]} />
      </div>

      {job.issues && job.issues.length > 0 && (
        <div className="bg-red-50 rounded border border-red-200 p-4">
          <h3 className="text-lg font-medium text-red-800 mb-4">Issues</h3>
          <ul className="list-disc pl-5 space-y-2 text-sm text-red-700">
            {job.issues.map((issue, index) => (
              <li key={`${issue.message}-${index}`}>
                <span className="font-semibold">{issue.severity}:</span> {issue.message}
                {issue.context && <span className="block text-xs mt-1 text-red-600">Context: {issue.context}</span>}
                {issue.recordIdentifier && <span className="block text-xs mt-1 text-red-600">Record: {issue.recordIdentifier}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

import { useState } from "react";
import {
  approvePlan,
  applyPlan,
  type ExecutionRun,
} from "../../../../../api/operationalGeneration";

type ProposalApplyDialogProps = {
  isOpen: boolean;
  onClose: () => void;
  proposalChecksum: string;
  planId: string;
  targetEnvironment: string;
}

export function ProposalApplyDialog({
  isOpen,
  onClose,
  proposalChecksum,
  planId,
  targetEnvironment,
}: ProposalApplyDialogProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<ExecutionRun | null>(null);

  if (!isOpen) return null;

  const handleApply = async () => {
    setLoading(true);
    setError(null);
    try {
      const approval = await approvePlan(
        proposalChecksum,
        planId,
        targetEnvironment,
      );
      const executionRun = await applyPlan(
        proposalChecksum,
        planId,
        approval.approval_id,
        targetEnvironment,
      );
      setRun(executionRun);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Apply failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-lg p-6 max-w-lg w-full">
        <h2 className="text-xl font-bold mb-4">Apply Proposal</h2>

        {run ? (
          <div>
            <div className="mb-4 p-4 bg-green-50 text-green-800 rounded">
              Apply started successfully! Run ID: {run.run_id}
            </div>
            <button
              onClick={onClose}
              className="px-4 py-2 bg-gray-200 rounded hover:bg-gray-300"
            >
              Close
            </button>
          </div>
        ) : (
          <div>
            <p className="mb-4">
              Are you sure you want to approve and apply this plan?
            </p>
            {error && (
              <div className="mb-4 p-3 bg-red-100 text-red-700 rounded">
                {error}
              </div>
            )}

            <div className="flex justify-end space-x-3">
              <button
                onClick={onClose}
                disabled={loading}
                className="px-4 py-2 bg-gray-200 rounded hover:bg-gray-300 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={() => { void handleApply(); }}
                disabled={loading}
                className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
              >
                {loading ? "Applying..." : "Approve & Apply"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

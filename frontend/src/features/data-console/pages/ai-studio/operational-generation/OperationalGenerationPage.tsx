import { useState } from "react";
import {
  createProposal,
  validateProposal,
  planProposal,
  type OperationalGenerationProposal,
  type ValidationResult,
  type OperationalWritePlan,
} from "../../../../../api/operationalGeneration";
import { ProposalApplyDialog } from "./ProposalApplyDialog";
import { RollbackAction } from "./RollbackAction";

export function OperationalGenerationPage() {
  const [assetId, setAssetId] = useState("source.mongodb.customers");
  const [recordsPerAsset, setRecordsPerAsset] = useState(5);
  const [seed, setSeed] = useState(42);
  const [proposal, setProposal] =
    useState<OperationalGenerationProposal | null>(null);
  const [validationResult, setValidationResult] =
    useState<ValidationResult | null>(null);
  const [plan, setPlan] = useState<OperationalWritePlan | null>(null);
  const [isApplyDialogOpen, setIsApplyDialogOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleCreate = async () => {
    try {
      setError(null);
      const res = await createProposal({
        assetIds: [assetId.trim()],
        recordsPerAsset,
        seed,
        mode: "DETERMINISTIC",
        scenarioName: "operational-generation",
      });
      setProposal(res);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to create proposal");
    }
  };

  const handleValidate = async () => {
    if (!proposal) return;
    try {
      setError(null);
      const res = await validateProposal(proposal.proposal_checksum);
      setValidationResult(res);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to validate proposal");
    }
  };

  const handlePlan = async () => {
    if (!proposal) return;
    try {
      setError(null);
      const res = await planProposal(proposal.proposal_checksum, "salt_123");
      setPlan(res);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to create plan");
    }
  };

  return (
    <div className="p-4 space-y-4">
      <h1 className="text-2xl font-bold">Operational Generation</h1>

      {error && (
        <div className="p-4 bg-red-100 text-red-800 rounded">{error}</div>
      )}

      <div className="grid max-w-2xl gap-3 sm:grid-cols-3">
        <label className="text-sm">
          Asset ID
          <input
            className="mt-1 w-full rounded border px-3 py-2"
            value={assetId}
            onChange={(event) => { setAssetId(event.target.value); }}
          />
        </label>
        <label className="text-sm">
          Records
          <input
            className="mt-1 w-full rounded border px-3 py-2"
            type="number"
            min={1}
            max={500}
            value={recordsPerAsset}
            onChange={(event) => { setRecordsPerAsset(Number(event.target.value)); }}
          />
        </label>
        <label className="text-sm">
          Seed
          <input
            className="mt-1 w-full rounded border px-3 py-2"
            type="number"
            min={0}
            value={seed}
            onChange={(event) => { setSeed(Number(event.target.value)); }}
          />
        </label>
      </div>

      <div className="flex flex-wrap gap-4">
        <button
          className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
          onClick={() => { void handleCreate(); }}
          disabled={!assetId.trim() || recordsPerAsset < 1 || recordsPerAsset > 500}
        >
          Generate Proposal
        </button>
        <button
          className="px-4 py-2 bg-gray-600 text-white rounded hover:bg-gray-700 disabled:opacity-50"
          onClick={() => { void handleValidate(); }}
          disabled={!proposal}
        >
          Validate Proposal
        </button>
        <button
          className="px-4 py-2 bg-purple-600 text-white rounded hover:bg-purple-700 disabled:opacity-50"
          onClick={() => { void handlePlan(); }}
          disabled={!proposal || validationResult?.state !== "VALID"}
        >
          Create Plan
        </button>
        <button
          className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50"
          onClick={() => { setIsApplyDialogOpen(true); }}
          disabled={!plan}
        >
          Apply Plan
        </button>
      </div>

      {proposal && (
        <div className="p-4 border rounded bg-gray-50">
          <h2 className="text-lg font-semibold mb-2">Proposal</h2>
          <pre className="text-sm overflow-auto max-h-40">
            {JSON.stringify(proposal, null, 2)}
          </pre>
        </div>
      )}

      {validationResult && (
        <div
          className={`p-4 border rounded ${validationResult.state === "VALID" ? "bg-green-50" : "bg-red-50"}`}
        >
          <h2 className="text-lg font-semibold mb-2">
            Validation Result ({validationResult.state})
          </h2>
          {validationResult.findings.length > 0 && (
            <ul className="list-disc pl-4 text-sm">
              {validationResult.findings.map((f, i) => (
                <li key={i}>
                  {f.message} ({f.code})
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {plan && (
        <div className="p-4 border rounded bg-gray-50">
          <h2 className="text-lg font-semibold mb-2">Write Plan</h2>
          <pre className="text-sm overflow-auto max-h-40">
            {JSON.stringify(plan, null, 2)}
          </pre>
        </div>
      )}

      {plan && proposal && (
        <ProposalApplyDialog
          isOpen={isApplyDialogOpen}
          onClose={() => { setIsApplyDialogOpen(false); }}
          proposalChecksum={proposal.proposal_checksum}
          planId={plan.plan_id}
          targetEnvironment="production"
        />
      )}

      <div className="mt-8 border-t pt-4">
        <h2 className="text-lg font-semibold mb-2">Rollback Management</h2>
        <RollbackAction />
      </div>
    </div>
  );
}

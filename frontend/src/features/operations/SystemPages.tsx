
/* eslint-disable @typescript-eslint/no-unnecessary-condition */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "wouter";
import { DatabaseZap, RefreshCw, Square, Trash2 } from "lucide-react";

import {
  applySeed,
  cancelSeedOperation,
  deleteSeedData,
  getOperationalDependency,
  getSeedOperation,
  getSeedStatus,
  listOperationalDependencies,
} from "../../api/operations";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { LoadingState } from "../../components/LoadingState";
import { PageHeader } from "../../components/PageHeader";
import { useToast } from "../../components/ToastProvider";
import {
  dangerButton,
  formatDate,
  inputClass,
  JsonBlock,
  KeyValue,
  Metric,
  Panel,
  primaryButton,
  ToneBadge,
} from "./shared";

const dependencyKey = ["operational-dependencies"] as const;
const seedKey = ["seed-status"] as const;
const seedOperationKey = ["seed-operation"] as const;

export function DependenciesPage() {
  const query = useQuery({
    queryKey: dependencyKey,
    queryFn: ({ signal }) => listOperationalDependencies(signal),
    refetchInterval: 5_000,
  });
  const dependencies = query.data ?? [];
  const healthy = dependencies.filter((item) => item.status === "HEALTHY").length;
  const unavailable = dependencies.filter((item) => item.status === "UNAVAILABLE").length;
  return (
    <div>
      <PageHeader title="Dependencies" description="Live infrastructure probes, worker heartbeats, event backlog, seed integrity, and AI-provider readiness." />
      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <Metric label="Healthy" value={healthy} />
        <Metric label="Unavailable" value={unavailable} />
        <Metric label="Total" value={dependencies.length} />
      </div>
      {query.isLoading && <LoadingState message="Checking dependencies..." />}
      {query.isError && <ErrorState message={query.error.message} />}
      {dependencies.length === 0 && !query.isLoading && <EmptyState title="No dependency evidence" description="The backend returned no operational dependency cards." />}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {dependencies.map((dependency) => (
          <Link key={dependency.id} href={`/system/dependencies/${dependency.id}`} className="block rounded-xl focus:outline-none focus:ring-2 focus:ring-slate-500">
            <Panel className="h-full transition hover:border-slate-400">
              <div className="flex items-start justify-between gap-3"><div><p className="text-xs font-medium uppercase tracking-wide text-slate-500">{dependency.category}</p><h2 className="mt-1 font-semibold text-slate-900">{dependency.name}</h2></div><ToneBadge value={dependency.status} /></div>
              <p className="mt-4 text-sm text-slate-600">{dependency.message}</p>
              <p className="mt-4 text-xs text-slate-500">Checked {formatDate(dependency.checkedAt)}</p>
            </Panel>
          </Link>
        ))}
      </div>
    </div>
  );
}

export function WorkflowWorkersPage() {
  const query = useQuery({
    queryKey: [...dependencyKey, "workflow-workers"],
    queryFn: ({ signal }) => listOperationalDependencies(signal),
    refetchInterval: 5_000,
  });
  const workers = (query.data ?? []).filter(
    (dependency) => dependency.category === "WORKER" || dependency.id === "temporal",
  );
  const healthy = workers.filter((worker) => worker.status === "HEALTHY").length;
  return (
    <div>
      <PageHeader
        title="Workflow Workers"
        description="Live Temporal readiness and durable worker heartbeats, refreshed every five seconds."
      />
      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <Metric label="Healthy" value={healthy} />
        <Metric label="Needs attention" value={workers.length - healthy} />
        <Metric label="Workers and engine" value={workers.length} />
      </div>
      {query.isLoading && <LoadingState message="Loading workflow worker status..." />}
      {query.isError && <ErrorState message={query.error.message} />}
      <div className="grid gap-4 lg:grid-cols-2">
        {workers.map((worker) => (
          <Panel key={worker.id}>
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
                  {worker.category === "WORKER" ? "Worker heartbeat" : "Workflow engine"}
                </p>
                <h2 className="mt-1 font-semibold text-slate-900">{worker.name}</h2>
              </div>
              <ToneBadge value={worker.status} />
            </div>
            <p className="mt-3 text-sm text-slate-600">{worker.message}</p>
            <p className="mt-3 text-xs text-slate-500">Checked {formatDate(worker.checkedAt)}</p>
            <div className="mt-4"><JsonBlock value={worker.details} /></div>
          </Panel>
        ))}
      </div>
    </div>
  );
}

export function DependencyDetailPage() {
  const params = useParams<{ dependencyId: string }>();
  const dependencyId = params.dependencyId ?? "";
  const query = useQuery({
    queryKey: [...dependencyKey, dependencyId],
    queryFn: ({ signal }) => getOperationalDependency(dependencyId, signal),
    enabled: Boolean(dependencyId),
    refetchInterval: 5_000,
  });
  if (query.isLoading) return <LoadingState message="Loading dependency evidence..." />;
  if (query.isError || !query.data) return <ErrorState message={query.error?.message ?? "Dependency not found"} />;
  const dependency = query.data;
  return (
    <div>
      <PageHeader title={dependency.name} description={dependency.message} />
      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="Readiness"><dl><KeyValue label="Status" value={<ToneBadge value={dependency.status} />} /><KeyValue label="Category" value={dependency.category} /><KeyValue label="Identifier" value={dependency.id} /><KeyValue label="Checked" value={formatDate(dependency.checkedAt)} /></dl></Panel>
        <Panel title="Evidence"><JsonBlock value={dependency.details} /></Panel>
      </div>
    </div>
  );
}

export function SeedDataPage() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [recordLimit, setRecordLimit] = useState(1_000);
  const query = useQuery({ queryKey: seedKey, queryFn: ({ signal }) => getSeedStatus(signal), refetchInterval: 10_000 });
  const operationQuery = useQuery({
    queryKey: seedOperationKey,
    queryFn: ({ signal }) => getSeedOperation(signal),
    refetchInterval: 2_000,
  });
  const applyMutation = useMutation({
    mutationFn: applySeed,
    onSuccess: (status) => {
      queryClient.setQueryData(seedKey, status);
      void queryClient.invalidateQueries({ queryKey: seedOperationKey });
      void queryClient.invalidateQueries({ queryKey: dependencyKey });
      toast({ title: "Seed data applied", description: status.digest, type: "success" });
    },
  });
  const cancelMutation = useMutation({
    mutationFn: () => cancelSeedOperation(),
    onSuccess: (operation) => {
      queryClient.setQueryData(seedOperationKey, operation);
      toast({ title: "Stop requested", description: operation.phase, type: "success" });
    },
  });
  const deleteMutation = useMutation({
    mutationFn: () => deleteSeedData(),
    onSuccess: (status) => {
      queryClient.setQueryData(seedKey, status);
      void queryClient.invalidateQueries({ queryKey: seedOperationKey });
      void queryClient.invalidateQueries({ queryKey: dependencyKey });
      toast({ title: "Seed data deleted", description: "Only seed-owned records were removed.", type: "success" });
    },
  });
  if (query.isLoading) return <LoadingState message="Loading seed status..." />;
  if (query.isError || !query.data) return <ErrorState message={query.error?.message ?? "Seed status unavailable"} />;
  const seed = query.data;
  const operation = operationQuery.data;
  const operationRunning = operation?.status === "RUNNING" || operation?.status === "CANCELLING";
  const mutationError = applyMutation.error ?? cancelMutation.error ?? deleteMutation.error;
  const progress = operation && operation.totalRecords > 0
    ? Math.min(100, Math.round((operation.processedRecords / operation.totalRecords) * 100))
    : 0;
  const validRecordLimit = Number.isInteger(recordLimit) && recordLimit >= 10 && recordLimit <= 1_000_000;
  return (
    <div>
      <PageHeader title="Seed Data" description="Choose a bounded dataset size, monitor progress, stop a stuck operation, or delete only seed-owned records." />
      {mutationError && <div className="mb-4"><ErrorState message={mutationError.message} /></div>}
      <div className="mb-6 grid gap-4 sm:grid-cols-3"><Metric label="Readiness" value={<ToneBadge value={seed.ready ? "HEALTHY" : "UNAVAILABLE"} />} /><Metric label="Version" value={seed.version} /><Metric label="Applied" value={formatDate(seed.appliedAt)} /></div>
      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="Dataset counts"><div className="grid grid-cols-2 gap-3">{Object.entries(seed.counts).map(([name, count]) => <div key={name} className="rounded-lg bg-slate-50 p-3"><p className="text-xs uppercase tracking-wide text-slate-500">{name}</p><p className="mt-1 text-xl font-semibold text-slate-900">{count}</p></div>)}</div><div className="mt-4 grid grid-cols-2 gap-3">{Object.entries(seed.scenarioCounts).map(([name, count]) => <div key={name} className="rounded-lg border border-slate-200 p-3"><p className="text-xs uppercase tracking-wide text-slate-500">{name}</p><p className="mt-1 text-lg font-semibold text-slate-900">{count}</p></div>)}</div><dl className="mt-4"><KeyValue label="Digest" value={<code className="text-xs">{seed.digest || "Not applied"}</code>} /><KeyValue label="Applied by" value={seed.appliedBy} /></dl></Panel>
        <Panel title="Governed operations">
          <p className="text-sm text-slate-600">
            The record limit caps each customer, product, and order dataset. Apply is idempotent;
            changing the limit replaces the active seed version without deleting unrelated data.
          </p>
          <div className="mt-5">
            <label className="block text-sm font-medium text-slate-700" htmlFor="seed-record-limit">
              Maximum records per seeded dataset
            </label>
            <input
              className={inputClass}
              id="seed-record-limit"
              type="number"
              min={10}
              max={1_000_000}
              step={10}
              value={recordLimit}
              disabled={operationRunning}
              onChange={(event) => { setRecordLimit(Number(event.target.value)); }}
            />
            <p className="mt-1 text-xs text-slate-500">
              Minimum 10, maximum 1,000,000. Customers and products remain capped by their JSON manifest totals.
            </p>
          </div>

          {operation && operation.status !== "IDLE" ? (
            <section className="mt-5 rounded-lg border border-slate-200 bg-slate-50 p-4" aria-live="polite">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    {operation.kind ?? "Seed operation"}
                  </p>
                  <p className="mt-1 text-sm font-medium text-slate-900">{operation.phase}</p>
                </div>
                <ToneBadge value={operation.status} />
              </div>
              {operation.totalRecords > 0 ? (
                <>
                  <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-200">
                    <div className="h-full bg-slate-900 transition-all" style={{ width: `${String(progress)}%` }} />
                  </div>
                  <p className="mt-2 text-xs text-slate-500">
                    {operation.processedRecords.toLocaleString()} / {operation.totalRecords.toLocaleString()} writes ({progress}%)
                  </p>
                </>
              ) : null}
              {operation.error ? <p className="mt-2 text-xs text-red-700">{operation.error}</p> : null}
            </section>
          ) : null}

          <div className="mt-5 flex flex-wrap gap-3">
            <button
              className={primaryButton}
              disabled={!validRecordLimit || operationRunning || applyMutation.isPending || deleteMutation.isPending}
              type="button"
              onClick={() => { applyMutation.mutate({ recordLimit }); }}
            >
              <DatabaseZap size={16} /> Apply seed
            </button>
            <button
              className={dangerButton}
              disabled={!operationRunning || cancelMutation.isPending || operation?.status === "CANCELLING"}
              type="button"
              onClick={() => { cancelMutation.mutate(); }}
            >
              <Square size={15} /> Stop process
            </button>
            <button
              className={dangerButton}
              disabled={operationRunning || applyMutation.isPending || deleteMutation.isPending}
              type="button"
              onClick={() => {
                if (window.confirm("Delete all records owned by the active seed version? This does not delete unrelated platform data.")) {
                  deleteMutation.mutate();
                }
              }}
            >
              <Trash2 size={16} /> Delete all seed data
            </button>
            <button
              className={dangerButton}
              disabled={!validRecordLimit || operationRunning || applyMutation.isPending || deleteMutation.isPending}
              type="button"
              onClick={() => {
                if (window.confirm("Delete the active seed version and reapply it with the selected record limit?")) {
                  applyMutation.mutate({ recordLimit, reset: true });
                }
              }}
            >
              <RefreshCw size={16} /> Reset and apply
            </button>
          </div>
          {!validRecordLimit ? <p className="mt-3 text-sm text-red-700">Enter a whole number from 10 through 1,000,000.</p> : null}
          {seed.requestedRecordLimit ? <p className="mt-3 text-xs text-slate-500">Last requested limit: {seed.requestedRecordLimit.toLocaleString()} records per dataset.</p> : null}
          {seed.validationErrors.length > 0 && <ul className="mt-5 list-disc space-y-1 pl-5 text-sm text-red-700">{seed.validationErrors.map((error) => <li key={error}>{error}</li>)}</ul>}
        </Panel>
      </div>
    </div>
  );
}

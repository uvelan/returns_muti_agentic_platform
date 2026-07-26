
/* eslint-disable @typescript-eslint/no-unnecessary-condition */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "wouter";
import { DatabaseZap, RefreshCw } from "lucide-react";

import {
  applySeed,
  getOperationalDependency,
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
  JsonBlock,
  KeyValue,
  Metric,
  Panel,
  primaryButton,
  ToneBadge,
} from "./shared";

const dependencyKey = ["operational-dependencies"] as const;
const seedKey = ["seed-status"] as const;

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
  const query = useQuery({ queryKey: seedKey, queryFn: ({ signal }) => getSeedStatus(signal), refetchInterval: 10_000 });
  const mutation = useMutation({
    mutationFn: applySeed,
    onSuccess: (status) => {
      queryClient.setQueryData(seedKey, status);
      void queryClient.invalidateQueries({ queryKey: dependencyKey });
      toast({ title: "Seed data applied", description: status.digest, type: "success" });
    },
  });
  if (query.isLoading) return <LoadingState message="Loading seed status..." />;
  if (query.isError || !query.data) return <ErrorState message={query.error?.message ?? "Seed status unavailable"} />;
  const seed = query.data;
  return (
    <div>
      <PageHeader title="Seed Data" description="Deterministic positive, expired-window, and in-transit scenarios for repeatable E2E validation." />
      {mutation.isError && <div className="mb-4"><ErrorState message={mutation.error.message} /></div>}
      <div className="mb-6 grid gap-4 sm:grid-cols-3"><Metric label="Readiness" value={<ToneBadge value={seed.ready ? "HEALTHY" : "UNAVAILABLE"} />} /><Metric label="Version" value={seed.version} /><Metric label="Applied" value={formatDate(seed.appliedAt)} /></div>
      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="Dataset counts"><div className="grid grid-cols-2 gap-3">{Object.entries(seed.counts).map(([name, count]) => <div key={name} className="rounded-lg bg-slate-50 p-3"><p className="text-xs uppercase tracking-wide text-slate-500">{name}</p><p className="mt-1 text-xl font-semibold text-slate-900">{count}</p></div>)}</div><div className="mt-4 grid grid-cols-2 gap-3">{Object.entries(seed.scenarioCounts).map(([name, count]) => <div key={name} className="rounded-lg border border-slate-200 p-3"><p className="text-xs uppercase tracking-wide text-slate-500">{name}</p><p className="mt-1 text-lg font-semibold text-slate-900">{count}</p></div>)}</div><dl className="mt-4"><KeyValue label="Digest" value={<code className="text-xs">{seed.digest || "Not applied"}</code>} /><KeyValue label="Applied by" value={seed.appliedBy} /></dl></Panel>
        <Panel title="Governed operations"><p className="text-sm text-slate-600">Apply is idempotent. Reset removes operational demo state, then reapplies the canonical source dataset.</p><div className="mt-5 flex flex-wrap gap-3"><button className={primaryButton} disabled={mutation.isPending} type="button" onClick={() => { mutation.mutate(false); }}><DatabaseZap size={16} /> Apply seed</button><button className={dangerButton} disabled={mutation.isPending} type="button" onClick={() => { if (window.confirm("Reset all operational demo returns, events, support cases, and AI traces?")) mutation.mutate(true); }}><RefreshCw size={16} /> Reset demo</button></div>{seed.validationErrors.length > 0 && <ul className="mt-5 list-disc space-y-1 pl-5 text-sm text-red-700">{seed.validationErrors.map((error) => <li key={error}>{error}</li>)}</ul>}</Panel>
      </div>
    </div>
  );
}

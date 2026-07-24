import type { ReactNode } from "react";

export const primaryButton = "inline-flex items-center justify-center rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50";
export const secondaryButton = "inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50";
export const dangerButton = "inline-flex items-center justify-center rounded-md bg-red-700 px-4 py-2 text-sm font-medium text-white transition hover:bg-red-600 disabled:cursor-not-allowed disabled:opacity-50";
export const inputClass = "mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-200";

export function Panel({ title, children, className = "" }: { title?: string; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-slate-200 bg-white p-5 shadow-sm ${className}`}>
      {title && <h2 className="mb-4 text-base font-semibold text-slate-900">{title}</h2>}
      {children}
    </section>
  );
}

export function ToneBadge({ value }: { value: string }) {
  const normalized = value.toUpperCase();
  const tone = normalized.includes("HEALTHY") || normalized.includes("COMPLETED") || normalized.includes("APPROVED") || normalized.includes("RESOLVED")
    ? "bg-emerald-50 text-emerald-700 ring-emerald-600/20"
    : normalized.includes("FAILED") || normalized.includes("REJECT") || normalized.includes("UNAVAILABLE") || normalized.includes("CANCEL") || normalized.includes("BLOCKED")
      ? "bg-red-50 text-red-700 ring-red-600/20"
      : normalized.includes("PENDING") || normalized.includes("REVIEW") || normalized.includes("DEGRADED") || normalized.includes("OPEN")
        ? "bg-amber-50 text-amber-700 ring-amber-600/20"
        : "bg-blue-50 text-blue-700 ring-blue-600/20";
  return <span className={`inline-flex rounded-md px-2 py-1 text-xs font-semibold ring-1 ring-inset ${tone}`}>{value}</span>;
}

export function KeyValue({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="border-b border-slate-100 py-3 last:border-0">
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-1 break-words text-sm text-slate-900">{value ?? "—"}</dd>
    </div>
  );
}

export function JsonBlock({ value }: { value: unknown }) {
  return <pre className="max-h-96 overflow-auto rounded-lg bg-slate-950 p-4 text-xs leading-5 text-slate-100">{JSON.stringify(value, null, 2)}</pre>;
}

export function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-slate-900">{value}</p>
    </div>
  );
}

export function formatDate(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : "—";
}

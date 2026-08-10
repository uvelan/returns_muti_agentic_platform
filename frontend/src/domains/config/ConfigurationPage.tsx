import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { configApi, type ConfigurationRelease } from "../../api/configuration";
import { useCapabilities } from "../../hooks/capabilityContext";
import { JsonView } from "./JsonView";

/**
 * The platform Configuration experience (Phase 19), read-only.
 *
 * **Two of nine tabs have a canonical endpoint.** `/api/config` serves the
 * runtime snapshot and the release ledger. Data Sources, Integrations,
 * Business, Modules, Security and Audit are served today by Data Console
 * routers, which Wave F retires -- binding this screen to them would make the
 * canonical Configuration UI depend on the very product it replaces, and every
 * such call would break at cutover. They are named as pending instead.
 *
 * **No promotion controls, and the reason is a live decision.** Two
 * configuration release lifecycles exist: the hardened `ReleaseService` that
 * recomputes checksums on VALIDATED->APPROVED and APPROVED->ACTIVE, which is
 * constructed nowhere outside a test file, and the hand-rolled transition
 * table in Data Console that production actually runs, which does not
 * recompute. Offering Approve or Activate here would silently bless whichever
 * one this screen happened to call, on every future promotion.
 *
 * **Redaction is server-side and stays there.** `redact_secret_values` scrubs
 * resolved secrets before the response is built and leaves `vault://`
 * references intact so an operator can see which secret a binding points at.
 * This screen adds no masking of its own; pretending the browser is a security
 * boundary would be worse than useless.
 */

const TABS = [
  "Overview",
  "Runtime",
  "Releases",
  "Data Sources",
  "Integrations",
  "Business",
  "Modules",
  "Security",
  "Audit",
] as const;
type Tab = (typeof TABS)[number];

const UNBACKED: Partial<Record<Tab, string>> = {
  "Data Sources":
    "Served today by the Data Console sources and browser routers, which Wave F retires. A canonical /api/config/sources does not exist yet.",
  Integrations: "No canonical endpoint. Phase 15 lists it; it has not been built.",
  Business: "No canonical endpoint. Phase 15 lists it; it has not been built.",
  Modules: "No canonical endpoint. Phase 15 lists it; it has not been built.",
  Security: "No canonical endpoint. Phase 15 lists it; it has not been built.",
  Audit: "No canonical endpoint. Phase 15 lists it; it has not been built.",
};

export function ConfigurationPage() {
  const { can } = useCapabilities();
  const [tab, setTab] = useState<Tab>("Overview");

  if (!can("config.runtime.read")) {
    return <p className="text-sm text-slate-600">You do not have access to configuration.</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Configuration</h1>
        <p className="mt-1 text-sm text-slate-600">
          Sources, integrations, business rules, runtime, and releases.
        </p>
      </header>

      <div role="tablist" aria-label="Configuration" className="flex flex-wrap gap-1 border-b border-slate-200">
        {TABS.map((name) => (
          <button
            key={name}
            role="tab"
            type="button"
            aria-selected={tab === name}
            onClick={() => { setTab(name); }}
            className={[
              "px-3 py-2 text-sm font-medium transition",
              tab === name
                ? "border-b-2 border-slate-900 text-slate-900"
                : "text-slate-500 hover:text-slate-800",
            ].join(" ")}
          >
            {name}
          </button>
        ))}
      </div>

      <TabBody tab={tab} canReadReleases={can("config.release.read")} />
    </div>
  );
}

function TabBody({ tab, canReadReleases }: { tab: Tab; canReadReleases: boolean }) {
  const unbacked = UNBACKED[tab];
  if (unbacked) return <p className="text-sm text-slate-500">{unbacked}</p>;

  switch (tab) {
    case "Overview":
      return <OverviewTab canReadReleases={canReadReleases} />;
    case "Runtime":
      return <RuntimeTab />;
    case "Releases":
      return <ReleasesTab canRead={canReadReleases} />;
    default:
      return null;
  }
}

function OverviewTab({ canReadReleases }: { canReadReleases: boolean }) {
  const runtime = useQuery({ queryKey: ["config", "runtime"], queryFn: configApi.runtime });
  const releases = useQuery({
    queryKey: ["config", "releases"],
    queryFn: configApi.releases,
    enabled: canReadReleases,
  });

  const active = (releases.data ?? []).find((r) => r.status === "ACTIVE");

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <Card title="Runtime snapshot">
          {runtime.isLoading ? (
            <p className="text-sm text-slate-500">Loading...</p>
          ) : runtime.error ? (
            // 503 here means the process has no snapshot loaded, which is a
            // real operational state worth showing rather than a blank card.
            <p className="text-sm text-red-700">{runtime.error.message}</p>
          ) : (
            <p className="text-sm text-emerald-700">Loaded and serving.</p>
          )}
        </Card>
        <Card title="Active release">
          {!canReadReleases ? (
            <p className="text-sm text-slate-600">Requires config.release.read.</p>
          ) : active ? (
            <p className="break-all font-mono text-xs text-slate-800">{active.release_id}</p>
          ) : (
            <p className="text-sm text-slate-600">No ACTIVE release found.</p>
          )}
        </Card>
        <Card title="Releases">
          <p className="text-2xl font-semibold text-slate-900">
            {canReadReleases ? (releases.data?.length ?? 0) : "-"}
          </p>
        </Card>
      </div>

      <p className="text-sm text-slate-500">
        Promotion controls are absent by design: two release lifecycles exist and which
        one is authoritative is an open decision. Approving or activating from here would
        pick one silently, on every future promotion.
      </p>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="text-xs uppercase tracking-wide text-slate-500">{title}</h2>
      <div className="mt-2">{children}</div>
    </section>
  );
}

function RuntimeTab() {
  const runtime = useQuery({ queryKey: ["config", "runtime"], queryFn: configApi.runtime });

  if (runtime.isLoading) return <p className="text-sm text-slate-500">Loading...</p>;
  if (runtime.error) return <p className="text-sm text-red-700">{runtime.error.message}</p>;

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-slate-900">Active validated snapshot</h2>
      <p className="mt-1 text-xs text-slate-500">
        Read from the running process, not rebuilt -- what is serving right now.
      </p>
      <div className="mt-3">
        <JsonView value={runtime.data} />
      </div>
    </div>
  );
}

function ReleasesTab({ canRead }: { canRead: boolean }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const releases = useQuery({
    queryKey: ["config", "releases"],
    queryFn: configApi.releases,
    enabled: canRead,
  });
  const detail = useQuery({
    queryKey: ["config", "release", selectedId],
    queryFn: () => configApi.release(selectedId ?? ""),
    enabled: selectedId !== null,
  });

  if (!canRead) {
    return <p className="text-sm text-slate-600">Viewing releases requires config.release.read.</p>;
  }
  if (releases.isLoading) return <p className="text-sm text-slate-500">Loading...</p>;
  if (releases.error) return <p className="text-sm text-red-700">{releases.error.message}</p>;

  const rows = releases.data ?? [];

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_24rem]">
      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="p-2">Release</th>
              <th className="p-2">Status</th>
              <th className="p-2">Approved by</th>
              <th className="p-2">Activated</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={4} className="p-3 text-slate-600">No releases.</td>
              </tr>
            ) : null}
            {rows.map((release, index) => (
              <tr
                key={release.release_id ?? String(index)}
                onClick={() => { setSelectedId(release.release_id ?? null); }}
                className="cursor-pointer border-t border-slate-200 hover:bg-slate-50"
              >
                <td className="p-2 font-mono text-xs">{release.release_id ?? "-"}</td>
                <td className="p-2">
                  <StatusBadge status={release.status} />
                </td>
                <td className="p-2">{release.approved_by ?? "-"}</td>
                <td className="p-2">
                  {release.activated_at ? new Date(release.activated_at).toLocaleString() : "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <aside className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-slate-900">Release detail</h2>
        {selectedId === null ? (
          <p className="mt-2 text-sm text-slate-600">Select a release.</p>
        ) : detail.isLoading ? (
          <p className="mt-2 text-sm text-slate-500">Loading...</p>
        ) : detail.error ? (
          <p className="mt-2 text-sm text-red-700">{detail.error.message}</p>
        ) : detail.data ? (
          <ReleaseDetail release={detail.data} />
        ) : null}
      </aside>
    </div>
  );
}

function ReleaseDetail({ release }: { release: ConfigurationRelease }) {
  return (
    <div className="mt-3 flex flex-col gap-3 text-sm">
      <Field label="Checksum" value={release.checksum ?? "-"} mono />
      <Field label="Created" value={format(release.created_at)} />
      <Field label="Validated" value={format(release.validated_at)} />
      <Field label="Approved" value={format(release.approved_at)} />
      <Field label="Approved by" value={release.approved_by ?? "-"} />
      <Field label="Activated" value={format(release.activated_at)} />
      <Field label="Superseded by" value={release.superseded_by ?? "-"} mono />
      {release.domains ? (
        <div>
          <p className="text-xs uppercase tracking-wide text-slate-500">Domains</p>
          <div className="mt-1">
            <JsonView value={release.domains} />
          </div>
        </div>
      ) : null}
    </div>
  );
}

function format(value: string | null | undefined): string {
  // A null lifecycle timestamp means the transition has not happened, which is
  // different from a missing field; both render as "-" but neither is invented.
  return value ? new Date(value).toLocaleString() : "-";
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className={mono ? "break-all font-mono text-xs text-slate-800" : "text-slate-800"}>
        {value}
      </p>
    </div>
  );
}

function StatusBadge({ status }: { status: string | undefined }) {
  const tone =
    status === "ACTIVE"
      ? "bg-emerald-100 text-emerald-800"
      : status === "SUPERSEDED" || status === "REJECTED"
        ? "bg-slate-100 text-slate-600"
        : "bg-amber-100 text-amber-800";
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${tone}`}>
      {status ?? "-"}
    </span>
  );
}

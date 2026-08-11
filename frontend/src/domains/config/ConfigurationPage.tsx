import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ALLOWED_PROMOTIONS,
  configApi,
  type ConfigurationRelease,
  type PromotionTarget,
} from "../../api/configuration";
import { useCapabilities } from "../../hooks/capabilityContext";
import { AgentsSection } from "./AgentsSection";
import { type CONFIG_SECTIONS, requireDomain } from "../registry";
import { useDomainSection } from "../useDomainSection";
import { JsonView } from "./JsonView";

/**
 * The platform Configuration experience (Phase 19).
 *
 * **Four of nine tabs have a canonical endpoint**, up from two. D3 added
 * `/api/config/sources` and `/api/config/audit`, both delegating to the Data
 * Console handlers rather than reimplementing them, so this screen reaches them
 * through the canonical path and nothing here breaks at cutover.
 *
 * Of the five that remain, three will never need one and say so rather than
 * claiming to be pending: business config and integrations are *already* served
 * -- `/runtime` returns the whole snapshot and they are fields on it -- and
 * modules would return `[]` forever because the kernel module registry is empty
 * by design. Security is not configuration: the role model is code, and
 * publishing the role-to-capability table would let a UI reimplement
 * authorization locally, which the capability layer exists to prevent.
 *
 * **Promotion controls exist now, and drive one lifecycle.** The blocker was
 * real -- two release lifecycles existed and a button would have silently
 * blessed whichever it happened to call. D3 settled it in favour of the graph
 * and deleted the other. The buttons offered are derived from
 * `ALLOWED_PROMOTIONS`, which mirrors the backend's `RELEASE_TRANSITIONS`, so
 * an operator is not shown a promotion that will be refused.
 *
 * **Redaction is server-side and stays there.** `redact_secret_values` scrubs
 * resolved secrets before the response is built and leaves `vault://`
 * references intact so an operator can see which secret a binding points at.
 * This screen adds no masking of its own; pretending the browser is a security
 * boundary would be worse than useless.
 */

const CONFIG_DOMAIN = requireDomain("/config");

/** Mirrors `CONFIG_DOMAIN.sections`; the registry drives the sidebar, this drives the body. */
type Tab = (typeof CONFIG_SECTIONS)[number];

/**
 * Tabs with no endpoint of their own, and why -- three of these are *already
 * served* rather than missing, which is a different statement and worth making.
 */
const UNBACKED: Partial<Record<Tab, string>> = {
  Integrations:
    "Already served. Integrations are fields on the runtime snapshot (configuration.integrations and configuration.runtime_integrations), visible on the Runtime tab. A second endpoint would duplicate them.",
  Business:
    "Already served. The runtime snapshot's configuration field is the business configuration; see the Runtime tab.",
  Modules:
    "No endpoint, deliberately. The kernel module registry is empty by design, so a /modules route would answer [] forever -- a shell that always says nothing is worse than an honest absence. A release's module list is reachable through its domain payloads.",
  Security:
    "Not configuration. The role model is code, and a caller's own grants are on /api/principal. Publishing the whole role-to-capability table would let this screen reimplement authorization locally, which is what the capability layer exists to prevent.",
};

export function ConfigurationPage() {
  const { can } = useCapabilities();
  // The section comes from the URL and the sidebar sets it, so the screen
  // holds no navigation state of its own.
  const tab = useDomainSection(CONFIG_DOMAIN) as Tab;

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
    case "Agents":
      return <AgentsSection />;
    case "Runtime":
      return <RuntimeTab />;
    case "Releases":
      return <ReleasesTab canRead={canReadReleases} />;
    case "Data Sources":
      return <SourcesTab />;
    case "Audit":
      return <AuditTab />;
    default:
      return null;
  }
}

function SourcesTab() {
  const sources = useQuery({ queryKey: ["config", "sources"], queryFn: configApi.sources });

  if (sources.isLoading) return <p className="text-sm text-slate-500">Loading...</p>;
  if (sources.error) return <p className="text-sm text-red-700">{sources.error.message}</p>;

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-slate-900">Configured sources</h2>
      <p className="mt-1 text-xs text-slate-500">
        Health is probed server-side. Secret references are shown as
        <code className="mx-1">vault://</code>
        pointers rather than resolved values.
      </p>
      <JsonView value={sources.data} />
    </div>
  );
}

function AuditTab() {
  const audit = useQuery({ queryKey: ["config", "audit"], queryFn: configApi.audit });

  if (audit.isLoading) return <p className="text-sm text-slate-500">Loading...</p>;
  if (audit.error) return <p className="text-sm text-red-700">{audit.error.message}</p>;

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-slate-900">Platform audit</h2>
      <p className="mt-1 text-xs text-slate-500">
        {/* The path says config; the records do not promise to be only config.
            They carry their own action and target. */}
        Not filtered to configuration -- these are platform audit records, and each carries
        its own action and target.
      </p>
      <JsonView value={audit.data} />
    </div>
  );
}

function OverviewTab({ canReadReleases }: { canReadReleases: boolean }) {
  const runtime = useQuery({ queryKey: ["config", "runtime"], queryFn: configApi.runtime });
  const releases = useQuery({
    queryKey: ["config", "releases"],
    queryFn: configApi.releases,
    enabled: canReadReleases,
  });

  // RELEASED, not ACTIVE. This searched for `"ACTIVE"` -- a status from the
  // Mongo lifecycle D3 deleted -- so it matched nothing and the card reported
  // "No ACTIVE release found" in every deployment, including ones with a
  // perfectly good published release.
  const active = (releases.data ?? []).find((r) => r.status === "RELEASED");

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
            <p className="text-sm text-slate-600">No RELEASED release found.</p>
          )}
        </Card>
        <Card title="Releases">
          <p className="text-2xl font-semibold text-slate-900">
            {canReadReleases ? (releases.data?.length ?? 0) : "-"}
          </p>
        </Card>
      </div>

      <p className="text-sm text-slate-500">
        Promotion is on the Releases tab. One lifecycle drives it -- DRAFT to VALIDATED to
        RELEASED, with ARCHIVED available as a retirement -- and publishing requires the
        configuration head revision, so two operators cannot both publish.
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
          <>
            <ReleaseDetail release={detail.data} />
            <PromotionControls release={detail.data} />
          </>
        ) : null}
      </aside>
    </div>
  );
}

function PromotionControls({ release }: { release: ConfigurationRelease }) {
  const { can } = useCapabilities();
  const queryClient = useQueryClient();
  const [headRevision, setHeadRevision] = useState("");

  const promote = useMutation({
    mutationFn: (status: PromotionTarget) =>
      configApi.promote(
        release.release_id ?? "",
        status,
        // Only sent when publishing -- the backend requires it there and
        // ignores it elsewhere. Parsed rather than coerced so a non-numeric
        // entry becomes "not supplied" and the backend's 422 explains it,
        // instead of silently becoming NaN.
        status === "RELEASED" ? Number.parseInt(headRevision, 10) : undefined,
      ),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["config", "releases"] }),
        queryClient.invalidateQueries({ queryKey: ["config", "release", release.release_id] }),
        // Publishing refreshes the process's active configuration, so the
        // runtime snapshot this screen shows is stale too.
        queryClient.invalidateQueries({ queryKey: ["config", "runtime"] }),
      ]);
    },
  });

  if (!can("config.release.promote")) {
    return (
      <p className="mt-3 border-t border-slate-200 pt-3 text-sm text-slate-600">
        Promoting a release requires config.release.promote.
      </p>
    );
  }

  const targets = ALLOWED_PROMOTIONS[release.status ?? ""] ?? [];
  if (targets.length === 0) {
    return (
      <p className="mt-3 border-t border-slate-200 pt-3 text-sm text-slate-600">
        {/* RELEASED and ARCHIVED are both ends of the line. A release leaves
            RELEASED by being superseded, which publishing its successor does --
            not by a promotion of its own. */}
        No promotion is available from {release.status ?? "this status"}.
      </p>
    );
  }

  return (
    <div className="mt-3 border-t border-slate-200 pt-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Promote</h3>

      {targets.includes("RELEASED") ? (
        <label className="mt-2 flex flex-col gap-1 text-sm">
          <span className="text-slate-700">Expected head revision</span>
          <input
            aria-label="Expected head revision"
            inputMode="numeric"
            value={headRevision}
            onChange={(event) => {
              setHeadRevision(event.target.value);
            }}
            className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          />
          <span className="text-xs text-slate-500">
            Required to publish. An optimistic check on the configuration head, so two
            operators publishing different releases cannot both win.
          </span>
        </label>
      ) : null}

      <div className="mt-2 flex flex-wrap gap-2">
        {targets.map((target) => (
          <button
            key={target}
            type="button"
            disabled={
              promote.isPending || (target === "RELEASED" && headRevision.trim() === "")
            }
            onClick={() => {
              promote.mutate(target);
            }}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
          >
            {target}
          </button>
        ))}
      </div>

      {promote.error ? (
        // Verbatim. The backend refuses for genuinely different reasons -- a
        // missing behaviour domain, an expired validation receipt, a checksum
        // that no longer matches, a head-revision conflict -- and each needs a
        // different response from the operator.
        <p role="alert" className="mt-2 text-sm text-red-700">
          {promote.error.message}
        </p>
      ) : null}
      {promote.isSuccess ? (
        <p className="mt-2 text-sm text-green-700">Promoted.</p>
      ) : null}
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

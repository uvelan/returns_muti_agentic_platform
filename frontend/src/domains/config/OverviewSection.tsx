import { useQuery } from "@tanstack/react-query";

import { configApi } from "../../api/configuration";
import { useCapabilities } from "../../hooks/capabilityContext";
import { runtimeSliceOf } from "./runtimeSlice";
import { UndecidedKeysPanel } from "./UndecidedKeysPanel";

/**
 * `/config/overview` -- release, head and adoption status per process class,
 * plus (CFG-4) the undecided-keys panel from `GET /api/config/packaged-drift`.
 */
export function OverviewSection({ canReadReleases }: { canReadReleases: boolean }) {
  const { can } = useCapabilities();
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
            <p className="break-all font-mono text-xs text-slate-800">{active.releaseId}</p>
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

      {can("config.release.write") ? (
        <UndecidedKeysPanel
          headRevision={runtime.data !== undefined ? runtimeSliceOf(runtime.data).headRevision : null}
        />
      ) : (
        <p className="rounded-lg border border-outline-variant bg-surface-container-low px-3 py-2 text-sm text-on-surface-variant">
          Viewing undecided packaged-configuration keys requires config.release.write.
        </p>
      )}
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

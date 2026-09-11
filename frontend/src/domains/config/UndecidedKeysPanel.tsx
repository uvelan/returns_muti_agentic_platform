import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { configApi, type PackagedDrift, type PackagedDriftDomain } from "../../api/configuration";
import { useCapabilities } from "../../hooks/capabilityContext";

/**
 * The Overview screen's undecided-keys panel -- `GET /api/config/packaged-drift`,
 * rendered.
 *
 * **A unit is per domain, and a bare name means `RETURN_PLATFORM`.** (RV
 * round 1, F4: this was mislabelled "F5" here and in the ledger -- CFG-3a's
 * actual F5 is a *backend* finding, "rollback narrower than 'any refusal'"
 * on `adopt_packaged`'s own `except` clause, and remains open, carried to
 * whichever lease next touches `backend/configuration` -- see
 * `.plan/tracks/CFG.ledger.md`'s CFG-4 step:10 note.)
 * `POST /adopt-packaged`'s own `units` vocabulary is `<key>` for a
 * `RETURN_PLATFORM` top-level key (`"discovery"`) or `<DOMAIN>/<unit>` for
 * anything else (`"AI_GATEWAY/tasks.T1"`) -- this panel states that
 * explicitly rather than leaving an operator to infer it from three
 * differently-shaped lists, and `unitId`/`unitLabel` below build and display
 * exactly that vocabulary.
 *
 * **F11: `would_adopt` answers "nothing merged yet" two different, both
 * correct ways across domains, and this panel renders both rather than
 * picking one.** With no active release, `RETURN_PLATFORM` has nothing to
 * merge against and reports `would_adopt: []`; `AI_GATEWAY`/
 * `DEPENDENCY_SIMULATION` fall back to the packaged file itself in that
 * state, so every packaged unit is reported. Neither is a bug in the other;
 * this panel shows each domain's own answer rather than asserting they
 * should agree.
 */

const DOMAIN_ORDER = ["RETURN_PLATFORM", "AI_GATEWAY", "DEPENDENCY_SIMULATION"] as const;

function unitId(domain: string, unit: string): string {
  return domain === "RETURN_PLATFORM" ? unit : `${domain}/${unit}`;
}

function unitLabel(domain: string, unit: string): string {
  return `${domain}: ${unit}`;
}

export function UndecidedKeysPanel({ headRevision }: { headRevision: number | null }) {
  const { can } = useCapabilities();
  const queryClient = useQueryClient();
  const drift = useQuery({ queryKey: ["config", "packaged-drift"], queryFn: configApi.packagedDrift });
  const [adopted, setAdopted] = useState<string | null>(null);
  const [pendingUnit, setPendingUnit] = useState<string | null>(null);

  const adopt = useMutation({
    mutationFn: (units: readonly string[]) => configApi.adoptPackaged(units, headRevision ?? 0),
    onSuccess: async (result, units) => {
      setAdopted(units.join(", "));
      setPendingUnit(null);
      await queryClient.invalidateQueries({ queryKey: ["config"] });
      return result;
    },
    onSettled: () => { setPendingUnit(null); },
  });

  const canAct = can("config.release.write");

  if (drift.isPending) {
    return <p className="text-sm text-on-surface-variant">Loading undecided keys...</p>;
  }
  if (drift.error !== null) {
    return (
      <p role="alert" className="text-sm text-error">
        {drift.error.message}
      </p>
    );
  }

  const data: PackagedDrift = drift.data;
  const domains = DOMAIN_ORDER.filter((domain) => domain in data);
  const undecidedRows = domains.flatMap((domain) =>
    data[domain].undecided.map((unit) => ({ domain, unit })),
  );
  const wouldAdoptRows = domains.flatMap((domain) =>
    data[domain].would_adopt.map((unit) => ({ domain, unit })),
  );

  /**
   * RV F1: this used to infer "no active release" from the unit's absence
   * from `would_adopt`. That inference is wrong by construction for exactly
   * the rows this panel exists to show: `_carry_forward`
   * (`packaged_adoption.py:228-243`) gives an undecided key
   * `merged[key] = _fill_absent_leaves(value, active[key])` -- the release's
   * *own* value, kept -- so `_would_adopt` (`:552-584`), which reports a key
   * only when `merged[key] == packaged[key]`, never reports an undecided key
   * that has an active release to be undecided against. On the live stack
   * every one of six undecided rows read "No active release... adopting is
   * refused" next to an enabled button that would, in fact, publish
   * immediately.
   *
   * The real signal for "is there an active release" is `headRevision`
   * (`null` exactly when `OverviewSection` has none to pass down -- see
   * `runtimeSliceOf`), not anything derived from `would_adopt`. With an
   * active release, an undecided key says what carry-forward actually did:
   * the release's own value is kept, `filled_leaves` names what the
   * packaged file would still fill in under it (computed only for undecided
   * keys, `packaged_adoption.py:623-629`), and the action's consequence is
   * stated plainly rather than implied by the confirm dialog alone.
   */
  function diffSummary(unit: string, domainDrift: PackagedDriftDomain, hasActiveRelease: boolean): string {
    if (!hasActiveRelease) {
      return "No active release to compare against yet -- adopting is refused until one exists.";
    }
    const filledUnder = domainDrift.filled_leaves.filter((leaf) => leaf === unit || leaf.startsWith(`${unit}.`));
    const kept =
      filledUnder.length > 0
        ? `The release's own value is kept for now; the packaged file would still fill ${String(filledUnder.length)} leaf${filledUnder.length === 1 ? "" : "s"} it leaves absent (${filledUnder.slice(0, 3).join(", ")}${filledUnder.length > 3 ? ", ..." : ""}).`
        : "The release's own value is kept for now.";
    return `${kept} Taking the packaged file for this whole key replaces every value the release holds for it.`;
  }

  return (
    <section className="premium-panel flex flex-col gap-3 p-4">
      <header>
        <p className="premium-kicker">Packaged configuration</p>
        <h3 className="mt-0.5 text-sm font-semibold text-on-surface">Undecided keys</h3>
        <p className="mt-1 max-w-3xl text-xs text-on-surface-variant">
          A key the packaged file and the active release disagree about, with neither an
          operator's edit nor a previous adoption on record. The list below is per domain --{" "}
          <code>AI_GATEWAY</code> and <code>DEPENDENCY_SIMULATION</code> keys are shown with their
          domain; a key with no domain prefix is a <code>RETURN_PLATFORM</code> top-level key, the
          same vocabulary <code>POST /adopt-packaged</code>&apos;s own <code>units</code> list takes.
        </p>
      </header>

      {adopted !== null ? (
        <p role="status" className="rounded-xl border border-primary/20 bg-secondary-container px-4 py-3 text-sm text-on-secondary-container">
          Adopted {adopted} from the packaged file as a new release.
        </p>
      ) : null}
      {adopt.error instanceof Error ? (
        <p role="alert" className="rounded-lg border border-error/20 bg-error-container px-3 py-2 text-sm text-on-error-container">
          {adopt.error.message}
        </p>
      ) : null}

      {undecidedRows.length === 0 ? (
        <p className="text-sm text-on-surface-variant">Nothing is undecided across any domain.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {undecidedRows.map(({ domain, unit }) => {
            const id = unitId(domain, unit);
            return (
              <li
                key={id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-outline-variant bg-surface-container-lowest px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-on-surface">{unitLabel(domain, unit)}</p>
                  <p className="mt-0.5 text-xs text-on-surface-variant">
                    {diffSummary(unit, data[domain], headRevision !== null)}
                  </p>
                </div>
                <button
                  type="button"
                  disabled={!canAct || headRevision === null || (adopt.isPending && pendingUnit === id)}
                  title={!canAct ? "config.release.write is required" : headRevision === null ? "No head revision to lock against" : undefined}
                  onClick={() => {
                    if (
                      !window.confirm(
                        `Take the packaged file's value for ${unitLabel(domain, unit)}? This publishes a new release.`,
                      )
                    ) {
                      return;
                    }
                    setPendingUnit(id);
                    adopt.mutate([id]);
                  }}
                  className="shrink-0 rounded-lg border border-outline-control bg-surface-container-lowest px-3 py-1.5 text-xs font-medium text-on-surface-variant transition hover:border-primary hover:text-primary disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {adopt.isPending && pendingUnit === id ? "Adopting..." : "Take packaged file"}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <div>
        <p className="premium-kicker">Would adopt on the next start</p>
        <p className="mt-0.5 text-xs text-on-surface-variant">
          Informational -- what a bootstrap or a full adoption would take from the packaged file
          right now, per domain. Not the same list as above: a key here may already be decided
          (an edited value the file happens to still agree with counts as decided, not undecided).
        </p>
        {wouldAdoptRows.length === 0 ? (
          <p className="mt-2 text-sm text-on-surface-variant">Nothing would be adopted from the packaged file right now.</p>
        ) : (
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {wouldAdoptRows.map(({ domain, unit }) => (
              <li
                key={unitId(domain, unit)}
                className="rounded-full bg-surface-container-low px-2.5 py-1 font-mono text-[11px] text-on-surface-variant"
              >
                {unitLabel(domain, unit)}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

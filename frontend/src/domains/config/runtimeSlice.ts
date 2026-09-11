import type { ValidationErrorItem } from "../../api/configuration";
import type { JsonObject } from "./DocumentEditor";
import { asObject, normalizeErrorPath } from "./jsonPath";

/**
 * The three things every typed screen reads off `GET /api/config/runtime`:
 * which release is active (the domain to publish against and re-clone from),
 * the head revision (`/publish`'s optimistic lock), and the behaviour
 * document itself. Shared so `snapshotOf`'s field names -- `release_id`,
 * `head_revision`, `configuration` -- are spelled once rather than once per
 * screen.
 */
export type RuntimeSlice = {
  readonly releaseId: string;
  readonly headRevision: number | null;
  readonly configuration: JsonObject;
  /**
   * CFG-6: this process's own environment (`app.state.settings.environment`),
   * added to `GET /api/config/runtime` so `/config/deployment` can render a
   * production-refused option disabled with the reason on it, before an
   * operator ever tries to publish one. `null` on a payload from before this
   * field existed (or an unrecognised value) -- treated as "unknown", never
   * as "production", so a screen does not falsely disable an option a real
   * production environment would allow.
   */
  readonly environment: string | null;
};

export function runtimeSliceOf(snapshot: Readonly<Record<string, unknown>>): RuntimeSlice {
  const releaseId = snapshot.release_id;
  const head = snapshot.head_revision;
  const environment = snapshot.environment;
  return {
    releaseId: typeof releaseId === "string" ? releaseId : "unknown",
    headRevision: typeof head === "number" ? head : null,
    configuration: asObject(snapshot.configuration as JsonObject | undefined),
    environment: typeof environment === "string" ? environment : null,
  };
}

/**
 * `ValidationErrorItem[]` (from `/validate`) keyed by path, for `Field`'s
 * `error` prop.
 *
 * RV F3: the backend's `_dotted_error_path` emits an array index bracketed
 * (`shipment_tracking.statuses[0].code`, per `releases.py`'s own docstring
 * example, `"agents[2].version", not "agents.2.version"`), but every screen
 * in this lease builds its lookup keys dotted
 * (`set(["shipment_tracking", "statuses", index, "code"], ...)` joins with
 * `.`, the same convention `DocumentEditor`'s own `childPath` uses). Without
 * normalising, `errorMap.get("shipment_tracking.statuses.0.code")` never
 * matches `"shipment_tracking.statuses[0].code"`, so the one indexed field
 * in this lease silently never lit up -- not lossy (the page-level
 * `ValidationErrors` list still shows it), but a no-op on exactly the path
 * whose convention differs. `normalizeErrorPath` is the same function
 * `DocumentEditor` normalises incoming `errors` through (CFG-3b A1); reused
 * here rather than re-solved, so both consumers of the same wire convention
 * agree by construction.
 */
export function errorsByPath(errors: readonly ValidationErrorItem[]): ReadonlyMap<string, string> {
  return new Map(errors.map((error) => [normalizeErrorPath(error.path), error.message]));
}

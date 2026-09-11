import type { ValidationErrorItem } from "../../api/configuration";
import type { JsonObject } from "./DocumentEditor";
import { asObject } from "./jsonPath";

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
};

export function runtimeSliceOf(snapshot: Readonly<Record<string, unknown>>): RuntimeSlice {
  const releaseId = snapshot.release_id;
  const head = snapshot.head_revision;
  return {
    releaseId: typeof releaseId === "string" ? releaseId : "unknown",
    headRevision: typeof head === "number" ? head : null,
    configuration: asObject(snapshot.configuration as JsonObject | undefined),
  };
}

/** `ValidationErrorItem[]` (from `/validate`) keyed by path, for `Field`'s `error` prop. */
export function errorsByPath(errors: readonly ValidationErrorItem[]): ReadonlyMap<string, string> {
  return new Map(errors.map((error) => [error.path, error.message]));
}

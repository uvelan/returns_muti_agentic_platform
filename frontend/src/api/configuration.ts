/**
 * `/api/config` -- the canonical Configuration surface (Phase 15).
 *
 * Three read routes, no mutations. The absent mutation surface is not an
 * oversight: the router's own comment records that `ReleaseService` -- the
 * lifecycle that verifies checksums on VALIDATED->APPROVED and
 * APPROVED->ACTIVE -- is constructed in exactly one place in the repository, a
 * test file, while production promotes releases through a hand-rolled
 * transition table with no checksum recompute. Which lifecycle is
 * authoritative is an open decision, so nothing here promotes anything.
 *
 * Secrets are scrubbed server-side by `redact_secret_values` before the
 * response is built, and a `vault://` reference is deliberately left intact so
 * an operator can see *which* secret a binding points at. This client does no
 * masking of its own -- doing so would imply the frontend is a security
 * boundary, which it is not.
 */

import { apiClient } from "./client";

/**
 * The graph release lifecycle, which is the authoritative one.
 *
 * **These were wrong.** They named the Mongo lifecycle -- `APPROVED`, `ACTIVE`,
 * `REJECTED` -- which D3 deleted in favour of the graph's after finding that
 * `ReleaseService` was constructed nowhere outside a test. Nothing failed,
 * because the field is optional and the values are strings: the Overview tab
 * searched for `status === "ACTIVE"`, matched nothing, and reported "No ACTIVE
 * release found" forever, which is indistinguishable from a deployment that
 * genuinely has none.
 *
 * Transitions, from `graph_repository.RELEASE_TRANSITIONS`:
 *   DRAFT     -> VALIDATED | ARCHIVED
 *   VALIDATED -> RELEASED  | ARCHIVED
 *   SUPERSEDED-> ARCHIVED
 * RELEASED is terminal-forward: a release leaves it by being superseded, which
 * publishing its successor does, not by a promotion of its own.
 */
export type ReleaseStatus = "DRAFT" | "VALIDATED" | "RELEASED" | "SUPERSEDED" | "ARCHIVED";

/** The promotions a caller can ask for. Mirrors `PromoteReleasePayload`. */
export type PromotionTarget = "VALIDATED" | "RELEASED" | "ARCHIVED";

/** Single-sourced from the backend table above rather than restated per button. */
export const ALLOWED_PROMOTIONS: Readonly<Record<string, readonly PromotionTarget[]>> = {
  DRAFT: ["VALIDATED", "ARCHIVED"],
  VALIDATED: ["RELEASED", "ARCHIVED"],
  SUPERSEDED: ["ARCHIVED"],
};

/**
 * `/releases` is typed `list[dict[str, Any]]` on the backend, so these mirror
 * `ConfigurationRelease` without claiming a guarantee the route does not make.
 * Lifecycle timestamps are nullable until their transition occurs.
 */
export type ConfigurationRelease = {
  readonly release_id?: string;
  readonly status?: ReleaseStatus;
  readonly checksum?: string;
  readonly created_at?: string;
  readonly updated_at?: string;
  readonly validated_at?: string | null;
  readonly approved_at?: string | null;
  readonly approved_by?: string | null;
  readonly activated_at?: string | null;
  readonly superseded_by?: string | null;
  readonly domains?: Readonly<Record<string, unknown>>;
};

export type RuntimeSnapshot = Readonly<Record<string, unknown>>;

export type ConfiguredSource = Readonly<Record<string, unknown>>;
export type AuditRecord = Readonly<Record<string, unknown>>;

async function unwrap<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await apiClient<T>(path, init);
  if (response.data === undefined || response.data === null) {
    throw new Error(`No data returned from ${path}.`);
  }
  return response.data;
}

export const configApi = {
  runtime: () => unwrap<RuntimeSnapshot>("/api/config/runtime"),
  releases: () => unwrap<ConfigurationRelease[]>("/api/config/releases"),
  release: (releaseId: string) =>
    unwrap<ConfigurationRelease>(`/api/config/releases/${releaseId}`),
  sources: () => unwrap<ConfiguredSource[]>("/api/config/sources"),
  audit: () => unwrap<AuditRecord[]>("/api/config/audit"),

  /**
   * Move a release along the lifecycle.
   *
   * `expectedHeadRevision` is required to publish and optional otherwise -- the
   * backend rejects a RELEASED promotion without it. It is an optimistic
   * concurrency check on the configuration head, so two operators publishing
   * different releases cannot both win.
   */
  promote: (releaseId: string, status: PromotionTarget, expectedHeadRevision?: number) =>
    unwrap<Readonly<Record<string, unknown>>>(
      `/api/config/releases/${encodeURIComponent(releaseId)}/promote`,
      {
        method: "POST",
        // `createHeaders` sets Accept but not Content-Type.
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          status,
          expected_head_revision: expectedHeadRevision ?? null,
        }),
      },
    ),
};

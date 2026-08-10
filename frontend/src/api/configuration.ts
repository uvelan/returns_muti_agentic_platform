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

export type ReleaseStatus =
  | "DRAFT"
  | "VALIDATED"
  | "APPROVED"
  | "ACTIVE"
  | "SUPERSEDED"
  | "REJECTED";

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

async function unwrap<T>(path: string): Promise<T> {
  const response = await apiClient<T>(path);
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
};

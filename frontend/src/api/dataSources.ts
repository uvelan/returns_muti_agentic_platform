import { apiClient } from "./client";

/**
 * `/api/config/sources` -- the configured sources and their probed health.
 *
 * Typed against `configuration/api/sources.py::SourceItem` / `SourceDetail`
 * rather than left as `Record<string, unknown>`, which is what the
 * configuration tab used: an untyped payload rendered as raw JSON is a viewer,
 * not a screen, and it cannot say which field is the health and which is the
 * ownership.
 *
 * **No credential can travel either way over this surface, and that is a
 * property of the backend models, not a promise made here.** `SourceItem` and
 * `SourceDetail` declare `extra="forbid"` and carry no credential field at all
 * -- `connectionIdentity` is `engine/database`, never a host, user or password
 * -- and `/api/config` scrubs every response through `redact_secret_values`,
 * which masks any resolved secret and preserves only `vault://` references.
 * There is correspondingly nothing here that sends a credential: the platform
 * resolves secrets server-side from Vault, so a credential field in this
 * console would be a field with nowhere to go.
 */

/**
 * `_health()` in `configuration/api/sources.py`, which is exhaustive: every
 * `DependencyStatus` maps to one of these four and the function has no other
 * exit. `UNKNOWN` is a real answer -- the probe could not say -- and must not be
 * treated as a healthy one.
 */
export type SourceHealth = "HEALTHY" | "DEGRADED" | "UNAVAILABLE" | "UNKNOWN";

export type SourceItem = {
  readonly id: string;
  readonly name: string;
  /** `MONGODB` | `PLATFORM` | `SQL_SERVER` | `NEO4J`, from `_definitions()`. */
  readonly engine: string;
  readonly environment: string;
  /** `AUTHORITATIVE` | `INTERNAL` | `DERIVED`. */
  readonly ownership: string;
  readonly health: SourceHealth;
  /** `READ_ONLY` | `WRITABLE`. What the platform may do, not what it may see. */
  readonly capability: string;
  readonly lastInventoryTime: string | null;
};

/** One table, collection or object the source exposes. */
export type SourceAssetSummary = {
  readonly assetId: string;
  readonly name: string;
  /** `TABLE` for SQL Server, `COLLECTION` for MongoDB. */
  readonly kind: string;
  readonly ownership: string;
  readonly authoritative: boolean;
  readonly writableInSandbox: boolean;
};

export type SourceDetail = SourceItem & {
  /** `engine/database`. Deliberately not a connection string. */
  readonly connectionIdentity: string;
  readonly inventoryTotals: { readonly assets: number; readonly records: number | null };
  readonly lastMetadataRefresh: string | null;
  /** The probe's `safe_message`, when it had one. */
  readonly dependencyWarnings: readonly string[];
  readonly assets: readonly SourceAssetSummary[];
};

export const dataSourcesApi = {
  async list(): Promise<SourceItem[]> {
    const response = await apiClient<SourceItem[]>("/api/config/sources");
    return response.data ?? [];
  },

  /**
   * One source, freshly probed.
   *
   * The handler runs the dependency probe on every request rather than reading
   * a cached status, so re-fetching this *is* the connection test -- there is no
   * separate "validate" route to call, and inventing one in the browser would
   * mean testing something other than what the platform reads through.
   */
  async get(sourceId: string): Promise<SourceDetail> {
    const path = `/api/config/sources/${encodeURIComponent(sourceId)}`;
    const response = await apiClient<SourceDetail>(path);
    if (response.data === null) {
      throw new Error(`No source returned from ${path}.`);
    }
    return response.data;
  },
};

import { apiClient } from "./client";

/**
 * Published graph-schema releases, and what moving between two of them costs.
 *
 * Releases are immutable, so going from one to the next is a generational step
 * rather than an edit. Until this surface existed the step was a pointer flip
 * with nothing to read first: an operator could move the runtime onto a release
 * that re-keys a node label and find out from the first associate who could not
 * locate an order.
 *
 * `migrationPlan` is a preview and writes nothing, so reviewing needs only read
 * rights. `activate` records the same plan and returns it, because whether a
 * rebuild is now owed is the consequence of the act and not a footnote to it.
 */

/**
 * Mirrors `dynamic_knowledge/release_migration.py::MigrationStrategy`.
 *
 * `BACKFILL` and `AFFECTED_SCOPE_RESYNC` are the two cheap tiers GRAPH-02 added:
 * before them every mapping change bought a complete rebuild, and a re-pointed
 * property was not seen as a change at all. `INCREMENTAL` is retained because
 * plans recorded before those classes existed must still deserialize -- the
 * planner no longer produces it, so a value read back is history, not a new
 * decision.
 */
export type MigrationStrategy =
  | "NO_CHANGE"
  | "INCREMENTAL"
  | "BACKFILL"
  | "AFFECTED_SCOPE_RESYNC"
  | "FULL_REBUILD";

export type GraphObjectKind =
  | "NODE_KEY_CONSTRAINT"
  | "RELATIONSHIP_MATCH_INDEX"
  | "DECLARED_CONSTRAINT"
  | "DECLARED_INDEX";

/** One constraint or index the migration creates or drops. */
export type GraphObject = {
  readonly kind: GraphObjectKind;
  readonly label: string;
  readonly properties: readonly string[];
  readonly detail: string;
};

export type ElementChange = {
  readonly element: string;
  readonly detail: string;
};

export type MigrationPlan = {
  /** Null when nothing is active yet -- always a build rather than a migration. */
  readonly from_release_id: string | null;
  readonly to_release_id: string;
  readonly strategy: MigrationStrategy;
  readonly node_labels_added: readonly string[];
  readonly node_labels_removed: readonly string[];
  readonly node_labels_changed: readonly ElementChange[];
  readonly relationships_added: readonly string[];
  readonly relationships_removed: readonly string[];
  readonly relationships_changed: readonly ElementChange[];
  readonly objects_to_create: readonly GraphObject[];
  readonly objects_to_drop: readonly GraphObject[];
  /** Why a rebuild is required. Empty unless the strategy is FULL_REBUILD. */
  readonly rebuild_reasons: readonly string[];
};

export type SchemaReleaseRow = {
  readonly configurationReleaseId: string;
  readonly configurationChecksum: string | null;
  readonly publishedBy: string | null;
  readonly publishedAt: string | null;
  readonly active: boolean;
};

export type SchemaReleaseList = {
  readonly releases: readonly SchemaReleaseRow[];
  readonly activeReleaseId: string | null;
};

const EMPTY: SchemaReleaseList = { releases: [], activeReleaseId: null };

export const schemaReleasesApi = {
  async list(): Promise<SchemaReleaseList> {
    const response = await apiClient<SchemaReleaseList>("/api/schema-releases");
    return response.data ?? EMPTY;
  },

  async migrationPlan(releaseId: string): Promise<MigrationPlan> {
    const response = await apiClient<MigrationPlan>(
      `/api/schema-releases/${encodeURIComponent(releaseId)}/migration-plan`,
    );
    if (!response.data) {
      throw new Error(`No migration plan returned for ${releaseId}.`);
    }
    return response.data;
  },

  async activate(releaseId: string): Promise<MigrationPlan> {
    const response = await apiClient<MigrationPlan>(
      `/api/schema-releases/${encodeURIComponent(releaseId)}/activate`,
      { method: "POST" },
    );
    if (!response.data) {
      throw new Error(`Activation of ${releaseId} returned no migration plan.`);
    }
    return response.data;
  },
};

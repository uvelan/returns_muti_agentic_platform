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
 * response is built, and a non-secret reference is deliberately left intact so
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
 * `/releases` is typed on the backend now, so these are guarantees rather than
 * hopes: `ConfigurationReleaseView` names exactly the six fields
 * `ConfigurationReleaseNode` persists, and the drift gate can see the shape.
 *
 * Six fields are gone from this type, and they were the reason it existed in
 * this shape: `updated_at`, `validated_at`, `approved_at`, `approved_by`,
 * `activated_at` and `superseded_by` have no writer anywhere in the platform.
 * Declaring them optional meant TypeScript could not object, so the governance
 * screen rendered permanent dashes for "who approved this" and "when did it go
 * live" -- reading as missing data rather than as an absent feature. A field
 * comes back here only once something persists it.
 *
 * `checksum` is `checksumSha256`. The old spelling never matched the wire, so
 * the one value that makes a release verifiable rendered as "-" while the API
 * was returning it.
 */
export type ConfigurationRelease = {
  readonly releaseId: string;
  readonly status: ReleaseStatus;
  readonly createdAt: string;
  readonly createdBy: string;
  readonly checksumSha256: string;
  readonly metadata?: Readonly<Record<string, unknown>>;
  readonly domains?: Readonly<Record<string, unknown>>;
};

export type RuntimeSnapshot = Readonly<Record<string, unknown>>;

export type AuditRecord = Readonly<Record<string, unknown>>;

/**
 * `POST /api/config/validate/{domain_key}` -- CFG-3a scope item 1, CFG-4's
 * first consumer. `errors` is pydantic's own `ValidationError.errors()`,
 * mapped to `{path, message, type}` by the backend's `_validation_errors` --
 * `path` is dot-plus-index, the same convention `DocumentEditor`'s `errors`
 * prop and the forms README's "Error path convention" already document, so a
 * typed screen can hand this list straight to `ValidationErrors`/`Field`
 * without translating it first.
 */
export type ValidationErrorItem = { readonly path: string; readonly message: string; readonly type: string };

export type ValidateResult = { readonly valid: boolean; readonly errors: readonly ValidationErrorItem[] };

/**
 * `POST /api/config/publish` -- CFG-3a scope item 2. Create-from-active,
 * canonical patch, VALIDATED, RELEASED, one call. `audit_ids` names the five
 * per-step records the backend writes (CREATED, DOMAIN_PATCHED, two
 * PROMOTED, PUBLISHED), in write order, each independently queryable via
 * `GET /api/config/audit?target=<release_id>`.
 *
 * Field names mirror `ConfigurationReleaseNode` (`graph_repository.py`) plus
 * the extra keys `publish_configuration` adds -- that model carries no alias
 * generator, so (unlike `ConfigurationReleaseView`) this stays snake_case on
 * the wire.
 */
export type PublishResult = {
  readonly release_id: string;
  readonly status: ReleaseStatus;
  readonly created_at: string;
  readonly created_by: string;
  readonly checksum_sha256: string;
  readonly metadata?: Readonly<Record<string, unknown>>;
  readonly domains: Readonly<Record<string, unknown>>;
  readonly head_revision: number;
  readonly audit_ids: readonly string[];
  readonly runtime_activation?: {
    readonly release_id: string;
    readonly checksum_sha256: string;
    readonly head_revision: number;
    readonly loaded_at: string;
  };
};

/**
 * `POST /api/config/adopt-packaged` -- CFG-3a scope item 3. Same response
 * shape as `PublishResult` plus `undecided`, the per-domain leftover after
 * this call's own units were taken.
 */
export type AdoptPackagedResult = PublishResult & {
  readonly undecided: Readonly<Record<string, readonly string[]>>;
};

/**
 * `GET /api/config/packaged-drift` -- the Overview screen's undecided-keys
 * panel. One entry per domain (`RETURN_PLATFORM`, `AI_GATEWAY`,
 * `DEPENDENCY_SIMULATION`); a unit name with no `DOMAIN/` prefix is a
 * `RETURN_PLATFORM` top-level key -- the vocabulary `POST /adopt-packaged`'s
 * own `units` list takes. (Not CFG-3a's F5, despite an earlier comment here
 * saying so -- see `UndecidedKeysPanel.tsx`'s own note, RV round 1 F4. The
 * real F5 is a backend finding and stays open.)
 *
 * **`would_adopt` answers "nothing merged yet" two different ways across
 * domains (CFG-3a F11), and both are correct for what each domain actually
 * is:** with no active release, `RETURN_PLATFORM` has nothing to merge
 * against and reports `[]`; `AI_GATEWAY`/`DEPENDENCY_SIMULATION` fall back to
 * the packaged file itself in that state, so `merged === packaged` and every
 * unit is reported. A caller must not read an empty `would_adopt` as "there
 * is nothing packaged for this domain" -- render what each domain's answer
 * actually is rather than assuming they agree.
 */
export type PackagedDriftDomain = {
  readonly undecided: readonly string[];
  readonly would_adopt: readonly string[];
  readonly filled_leaves: readonly string[];
};

export type PackagedDrift = Readonly<Record<string, PackagedDriftDomain>>;

/**
 * `ACTIVATED != LIVE` -- contract C5, as one answer an operator can act on.
 *
 * Promoting a release moves the graph pointer and nothing else. The API
 * process, the workers and the model the Order Agent calls each adopt on their
 * own poll, so until every required process class has reported the activated
 * revision the platform is running two releases at once. `ACTIVATING` is that
 * window, and it is a normal state rather than a fault.
 *
 * Mirrors `configuration/process_adoption.py::ReleaseAdoptionState`. The route
 * is declared `dict[str, Any]` on the backend, so these types are the contract
 * this client asserts rather than one the generated schema could give it.
 */
export type ReleaseAdoptionStatus = "LIVE" | "ACTIVATING" | "NO_ACTIVE_RELEASE";

export type ProcessAdoptionRecord = {
  readonly process_class: string;
  readonly instance_id: string;
  readonly release_id: string;
  readonly head_revision: number;
  readonly adopted_at: string;
  readonly reported_at: string;
  readonly source: string;
};

export type ProcessClassAdoption = {
  readonly process_class: string;
  readonly required: boolean;
  readonly adopted: boolean;
  /**
   * `live_instances === 0` is why `adopted` is false for a class nothing is
   * running. Reported separately from `adopted_instances` so "not deployed" and
   * "deployed and behind" are two different answers -- they call for opposite
   * responses, and a single "not adopted" badge collapses them.
   */
  readonly live_instances: number;
  readonly adopted_instances: number;
  readonly instances: readonly ProcessAdoptionRecord[];
};

export type ReleaseAdoptionState = {
  readonly status: ReleaseAdoptionStatus;
  readonly activated_release_id: string | null;
  readonly activated_head_revision: number | null;
  /**
   * The classes the platform is waiting on, by name. Named rather than counted
   * because "3 of 5" does not tell an operator which container to look at.
   */
  readonly pending_process_classes: readonly string[];
  readonly process_classes: readonly ProcessClassAdoption[];
  readonly evaluated_at: string;
};

/** Which of the two reasons a class has not adopted. They call for opposite acts. */
export function adoptionGap(
  adoption: ProcessClassAdoption,
): "ADOPTED" | "NOT_DEPLOYED" | "BEHIND" | "PARTIAL" {
  if (adoption.adopted) return "ADOPTED";
  if (adoption.live_instances === 0) return "NOT_DEPLOYED";
  return adoption.adopted_instances === 0 ? "BEHIND" : "PARTIAL";
}

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
  // `/api/config/sources` is not here. It moved to `api/dataSources.ts`, typed
  // against `SourceItem` / `SourceDetail`, when Data Sources stopped being a
  // configuration tab -- one untyped `Record<string, unknown>` reader for it
  // was what made the old tab a JSON viewer instead of a screen.
  audit: () => unwrap<AuditRecord[]>("/api/config/audit"),

  /**
   * Which processes are actually running the activated release.
   *
   * 503s rather than answering when `process_adoption_store` is absent from app
   * state, which is a real state -- reporting is unavailable, not "everything
   * has adopted" -- and is why callers must not fall back to an empty state on
   * failure.
   */
  adoption: () => unwrap<ReleaseAdoptionState>("/api/config/adoption"),

  /**
   * Open a draft release, cloned from the active release.
   *
   * The only writable release state is DRAFT, so this is the entry point to
   * changing any behaviour domain -- the AI provider editor rides it the same
   * way the Configuration domain's own screens do.
   */
  createRelease: (releaseId: string) =>
    unwrap<Readonly<Record<string, unknown>>>("/api/config/releases", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ release_id: releaseId, from_active: true }),
    }),

  /**
   * Merge-patch one behaviour domain of a draft (RFC 7396). The backend
   * validates the *result* against the domain's full model, so a patch that
   * would leave the configuration invalid is a 422 and nothing is stored.
   */
  patchDomain: (releaseId: string, domainKey: string, patch: Readonly<Record<string, unknown>>) =>
    unwrap<Readonly<Record<string, unknown>>>(
      `/api/config/releases/${encodeURIComponent(releaseId)}/domains/${encodeURIComponent(domainKey)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ patch }),
      },
    ),

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

  /**
   * Check a payload or a patch against the active release, with no write.
   * Exactly one of `payload`/`patch` -- the backend 422s on both or neither.
   * Read roles, not write: safe to call on every keystroke.
   */
  validateDomain: (
    domainKey: string,
    body: { payload: Readonly<Record<string, unknown>> } | { patch: Readonly<Record<string, unknown>> },
  ) =>
    unwrap<ValidateResult>(`/api/config/validate/${encodeURIComponent(domainKey)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  /**
   * Draft, patch and publish one behaviour domain in a single call -- the
   * typed screens' write path, replacing the four-round-trip
   * `createRelease`/`patchDomain`/`promote`x2 sequence `runPublishPipeline`
   * drives for the JSON editors. `expectedHeadRevision` is the optimistic
   * lock on the configuration head; `releaseId` is optional (the server
   * assigns one when omitted).
   */
  publish: (options: {
    domainKey: string;
    patch: Readonly<Record<string, unknown>>;
    expectedHeadRevision: number;
    releaseId?: string;
    note?: string;
  }) =>
    unwrap<PublishResult>("/api/config/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        release_id: options.releaseId ?? null,
        domain_key: options.domainKey,
        patch: options.patch,
        expected_head_revision: options.expectedHeadRevision,
        note: options.note ?? null,
      }),
    }),

  /**
   * Publish a release adopting the named packaged units -- the API's answer
   * to a `packaged_configuration_not_adopted` warning. `units` is
   * `<key>` (a `RETURN_PLATFORM` top-level key) or `<DOMAIN>/<unit>`
   * (`AI_GATEWAY/tasks.T1`), the same vocabulary `packagedDrift` reports.
   */
  adoptPackaged: (units: readonly string[], expectedHeadRevision: number) =>
    unwrap<AdoptPackagedResult>("/api/config/adopt-packaged", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ units, expected_head_revision: expectedHeadRevision }),
    }),

  /** The Overview screen's undecided-keys panel: `{undecided, would_adopt, filled_leaves}` per domain. */
  packagedDrift: () => unwrap<PackagedDrift>("/api/config/packaged-drift"),
};

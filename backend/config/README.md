# backend/config

Canonical, manifest-driven configuration for the unified return platform (design doc §3). See
`return_platform/configuration/README.md` for how this directory is loaded, validated, and
resolved into a `RuntimeSnapshot`.

## manifest.yaml

The single authoritative index. `schema_version` must be one of
`configuration/application/loader.py::SUPPORTED_MANIFEST_SCHEMA_VERSIONS` (currently `"2.0"`
only); anything else fails startup rather than silently loading as the current version.
`release_id` and `status` (`DRAFT`/`VALIDATED`/`APPROVED`/`ACTIVE`/`SUPERSEDED`) describe the
manifest's own release. `modules` maps every manifest ID to a `path` relative to this directory —
**a YAML file under this tree that is not listed here is never loaded**, regardless of its
contents; there is no directory globbing anywhere in the loader.

## Module document shape

Every file referenced from `manifest.yaml` is a module document with these top-level keys:

| Key | Required | Meaning |
|---|---|---|
| `module_id` | yes | Must equal the manifest key that references this file. |
| `module_type` | yes | Must match the manifest key's prefix (`agent.*` → `AGENT`, `policy.*` → `POLICY`, `workflow.*` → `WORKFLOW`, `sync.*` → `SYNC`, `source.*` → `SOURCE`, `mapping.*` → `MAPPING`, `graph.*` → `GRAPH`, `platform.*` → `PLATFORM`, `integration.*` → `INTEGRATION`). |
| `schema_version` | no | Version of this module's own payload shape. |
| `configuration_version` | no | Version of this module's configuration content. |
| `owner` | no | Team or system responsible for this module. |
| `status` | no | Free-text lifecycle status for the module document itself. |
| `dependencies` | no | List of `{module_id, version_constraint}`. Every `module_id` must be another manifest entry, must not equal this module's own ID (no self-dependency), and the resulting dependency graph must be acyclic — all three are enforced by `ConfigurationValidator`, not just at load time. |
| `payload` | module-type-dependent | Routed into the matching canonical domain — see below. |

## Where each module type ends up

| `module_type` | Directory | Routed into (`configuration/domain/`) |
|---|---|---|
| `AGENT` | `agents/` | `ModulesConfig` + `AgentsConfig` |
| `WORKFLOW` | `workflows/` | `ModulesConfig` + `WorkflowConfig` |
| `SOURCE` | `sources/` | `ModulesConfig` + `SourcesConfig` (payload must include `connector_type`; `access_mode` must be read-only if set) |
| `GRAPH` | `graph/`, `dynamic_knowledge/` | `ModulesConfig` + `GraphConfig.graphs` |
| `MAPPING` | `mappings/` | `ModulesConfig` + `GraphConfig.mappings` — never treated as a graph schema |
| `SYNC` | `sync/` | `ModulesConfig` + `GraphConfig.sync` — never treated as a graph schema |
| `POLICY` | `policies/` | `ModulesConfig` only — a policy is never mapped into `IntegrationsConfig` |
| `PLATFORM` | `platform/` | `ModulesConfig` + `SystemStoreConfig` (for `platform.system_store`) or `PlatformConfig` |
| `INTEGRATION` | (none declared yet) | `ModulesConfig` + `IntegrationsConfig` |

A malformed payload for any of these raises `ConfigurationValidationError` immediately — translation
is fail-closed, never a logged warning that silently drops the module from its canonical domain.

## Singleton compatibility files

Two files are loaded by explicit name rather than through the manifest, because they predate the
manifest mechanism and are being migrated incrementally:

- `ai_gateway.yaml` → `AiConfig` (routes, tasks, providers).
- `returns/production.yaml` → its `features` block feeds `FeaturesConfig`; its `platform` block
  feeds `PlatformConfig`.

No other file is loaded this way. Adding a new singleton requires an explicit new call in
`compatibility.py`, not a naming convention.

## Directories

- `agents/`, `policies/`, `workflows/`, `sync/`, `sources/`, `mappings/`, `graph/`, `platform/` —
  one file per manifest entry, named after the module.
- `dynamic_knowledge/` — Dynamic Knowledge schemas and internal-store manifests. A schema here is
  only authoritative if a `GRAPH` module in `manifest.yaml` points at it; an unreferenced file in
  this directory is never loaded, even though the directory also holds files like
  `active-schema.example.yaml` that exist purely as authoring references.
- `data_platform/` — canonical mappings, graph projection, sources, and sync-pipeline
  configuration for the data platform surfaces that have not yet migrated onto the manifest model.
- `seed/`, `live_validation/` — fixtures and validation configuration for local/dev seeding, not
  production runtime configuration.
- `schema_registry.yaml`, `data_assets.yaml`, `dependency_simulation.yaml` — governance and
  dependency-simulation inputs consumed directly by `Settings`, independent of the manifest.

## Adding a module

1. Write the module document under the directory matching its type, with `module_id` equal to the
   manifest key you intend to use and `module_type` matching that key's prefix.
2. Add the manifest entry in `manifest.yaml` pointing at the file's path (relative to this
   directory).
3. If the module depends on another, list it under `dependencies` — the referenced `module_id` must
   already be a manifest entry.
4. Run the configuration test suite (`backend/tests/configuration/`) — `test_loader_and_compatibility`
   and `test_validator_smoke` exercise the real files in this directory end to end.

## Declared but not yet loaded

Wave G2 asked for legacy compatibility configuration to be removed once no
production consumer remained. Scanned, there is none: every entry here is either
read at runtime or named by the target design as intended-but-unwired. Those two
states look identical from the filesystem, so the second is listed:

- `policies/` -- `candidate_scoring.yaml`, `clarification.yaml`, `privacy.yaml`,
  `return_eligibility.yaml`. Named in the target design's configuration tree.
  No code loads them.
- `live_validation/data_assets.sampling.yaml` -- named in the target design.
  No code loads it. (The `live_validation` string elsewhere in the repository is
  a coincidence: two validation scripts use it in *database* names.)

These are unimplemented design, not dead compatibility shims, so G2 deleted
nothing. Anyone treating an unreferenced file here as dead should check this
list first.

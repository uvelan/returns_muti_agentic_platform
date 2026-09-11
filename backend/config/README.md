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
| `dependencies` | no | List of `{module_id, version_constraint}`, for documentation purposes only — `ConfigurationLoader` (the only loader left; see below) does not resolve or validate this field. |
| `payload` | module-type-dependent | Module-specific content. |

Which directory holds which `module_type` (`AGENT` → `agents/`, `WORKFLOW` → `workflows/`,
`SOURCE` → `sources/`, `GRAPH` → `graph/`/`dynamic_knowledge/`, `MAPPING` → `mappings/`,
`SYNC` → `sync/`, `PLATFORM` → `platform/`) is enforced structurally by `ConfigurationLoader`
(manifest ID prefix must match the document's `module_type`). Routing a loaded module's `payload`
into a canonical per-domain model (`AgentsConfig`, `SourcesConfig`, `GraphConfig`, …) was
`application/compatibility.py::LegacyCompatibilityAdapter`'s job; that translation path was
retired in CFG-1 (see below) because no process ever constructed it.

## What actually runs (audited 2026-09-11)

`ai_gateway/` and `returns/` are not loaded through the manifest, and as of CFG-1 there is no other
loader for them either: what every process actually runs is the RELEASED release in the Neo4j
configuration graph, whose three domains are published from `returns/` (`RETURN_PLATFORM`),
`ai_gateway/` (`AI_GATEWAY`) and `dependency_simulation.yaml` (`DEPENDENCY_SIMULATION`) by
`return_platform/configuration/cli/bootstrap_graph_configuration.py` at every stack start, and
edited afterwards through `/api/config` and the Configuration, Support Template and AI Control
Center screens. See `return_platform/configuration/README.md` (Precedence) for the carry-forward
rules that decide whether an edited packaged file reaches a deployment that already has a release,
and `docs/configuration/DEFERRED_DESIGN.md` for `feature_flags`/`extensions` (retired in CFG-1,
D-CFG-2 -- neither block was ever read).

Before CFG-1, `ai_gateway/` and `returns/` (then single files, `ai_gateway.yaml` and
`returns/production.yaml`) were also loaded a second way -- by explicit name, through
`ConfigurationLoader.load_file`, from `application/compatibility.py::LegacyCompatibilityAdapter` --
to build a synthetic `RuntimeSnapshot` for `tests/configuration/` only; no process constructed that
path. CFG-1 retired `compatibility.py` along with `precedence.py`, `adapters.py` and `validator.py`,
so that second loading path no longer exists.

## Composed directories (CFG-2)

`returns/` and `ai_gateway/` are each a directory now, composed at load time by
`return_platform/configuration/composition.py::compose_configuration_document` -- the same idea as
`manifest.yaml` above, one level down: every file under the directory must be named by the
directory's own `index.yaml`, and nothing globs.

`index.yaml` carries:

- the document-level keys directly (for `returns/`: `schema_version`, `assumption_set_version`; for
  `ai_gateway/`: `schemaVersion`, `domain`, plus the five cross-task sections that are not split
  further -- `circuitBreaker`, `retry`, `rateLimits`, `providerLimits`, `modelContexts`);
- `parts:`, an ordered list of relative paths, each a whole top-level section (`returns/`'s
  `agents.yaml`, `discovery.yaml`, …);
- `entries:`, a mapping of section name to an ordered list of files whose own content becomes one
  entry each, keyed by filename stem (`ai_gateway/`'s `entries.tasks:` lists `tasks/<TASK_ID>.yaml`,
  25 files, one per task id -- dotted ids like `support.message.classify.v1` included, since
  `Path.stem` only strips the last suffix).

Four ways to get it wrong, every one a `ValueError` naming every offending file:

1. No `index.yaml` in the directory.
2. `index.yaml` lists a path that does not exist.
3. A `*.yaml`/`*.yml` file exists under the directory that no list names -- the same no-globbing
   rule `manifest.yaml` enforces above, restated at this level: an unlisted file is never loaded,
   regardless of its contents.
4. A section declared twice -- inline in `index.yaml` and in a part file, in two part files, or an
   `entries` stem colliding with another under `casefold()`.

`return_configuration.py::load_return_configuration` and `ai/routing/tasks.py::load_ai_gateway_configuration`
both accept a file (today's single-document shape, unchanged) or a directory; nothing that reads
`Settings.return_configuration_path` / `Settings.ai_gateway_configuration_path` has to know which.

## Directories

- `agents/`, `workflows/`, `sync/`, `sources/`, `mappings/`, `graph/`, `platform/` —
  one file per manifest entry, named after the module.
- `dynamic_knowledge/` — Dynamic Knowledge schemas. A schema here is only authoritative if a
  `GRAPH` module in `manifest.yaml` points at it; an unreferenced file in this directory is never
  loaded, even though the directory also holds files like `active-schema.example.yaml` that exist
  purely as authoring references.
- `data_platform/` — canonical mappings, graph projection, sources, and sync-pipeline
  configuration for the data platform surfaces that have not yet migrated onto the manifest model.
- `seed/` — fixtures for local/dev seeding, not production runtime configuration.
- `schema_registry.yaml`, `data_assets.yaml`, `dependency_simulation.yaml` — governance and
  dependency-simulation inputs consumed directly by `Settings`, independent of the manifest.

## Adding a module

1. Write the module document under the directory matching its type, with `module_id` equal to the
   manifest key you intend to use and `module_type` matching that key's prefix.
2. Add the manifest entry in `manifest.yaml` pointing at the file's path (relative to this
   directory).
3. If the module depends on another, list it under `dependencies` for documentation purposes --
   nothing validates it at load time (see `return_platform/configuration/README.md`).
4. Run the configuration test suite (`backend/tests/configuration/`) against the real files in
   this directory.

## Removed as dead (CFG-1)

`policies/` (four files), `live_validation/data_assets.sampling.yaml`,
`dynamic_knowledge/internal_manifests/` (four files) and `reasoning.yaml` were named by the target
design but had zero readers in `backend/src`. They were deleted in CFG-1 (decision D-CFG-1); the
rule each one described, and where the equivalent live rule is (or that none exists), is recorded
in `docs/configuration/DEFERRED_DESIGN.md`.

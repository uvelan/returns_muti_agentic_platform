# backend/config

Canonical, release-published configuration for the unified return platform (design doc §3). See
`return_platform/configuration/README.md` for how this directory is loaded, validated, and
resolved into a `RuntimeSnapshot`.

## manifest.yaml (CFG-5b: read by nothing)

`configuration/application/loader.py::ConfigurationLoader` -- the only thing that ever read this
file -- is deleted as of CFG-5b. The Agents editing API was its last consumer: agent modules used
to live under `agents/`, one file per manifest entry, edited through the loader; CFG-5's own
research (`.plan/tracks/CFG.ledger.md` step:05) traced every runtime consumer of that system and
found none outside the editing API itself -- `AgentRegistry.build()` and every agent class read
only the live `RETURN_PLATFORM.agents["<id>"]` section (`returns/agents.yaml`, composed into the
release), never a manifest module. `workflow.return_session`, `sync.order_partial`,
`sync.order_full`, `source.sales_inv`, `mapping.sales_inv_order` and `graph.order_discovery` were
already loaded by nothing at all before CFG-5b (CFG-5's grep found zero consumers), and are deleted
with their target files (`workflows/`, `sync/`, `sources/`, `mappings/sales_inv_order.yaml`,
`graph/order_discovery.yaml`).

`manifest.yaml` itself is kept, trimmed to its one remaining entry
(`platform.system_store`), rather than deleted outright -- see the file's own header comment for
why: that entry was *already* inert before CFG-5b (`platform/system_store/manifest_loader.py`
reads `platform/system_store.yaml` directly through `Settings.system_store_manifest_path`, never
through this manifest), so keeping or deleting the file decides nothing about any live code path
either way, and deleting `platform/system_store/manifest_loader.py` itself is outside this lease.

## Module document shape (historical -- no live loader reads this shape any more)

Before CFG-5b, every file referenced from `manifest.yaml` was a module document with these
top-level keys, enforced by the now-deleted `ConfigurationLoader`:

| Key | Required | Meaning |
|---|---|---|
| `module_id` | yes | Had to equal the manifest key that referenced the file. |
| `module_type` | yes | Had to match the manifest key's prefix (`agent.*` → `AGENT`, `policy.*` → `POLICY`, `workflow.*` → `WORKFLOW`, `sync.*` → `SYNC`, `source.*` → `SOURCE`, `mapping.*` → `MAPPING`, `graph.*` → `GRAPH`, `platform.*` → `PLATFORM`, `integration.*` → `INTEGRATION`). |
| `schema_version` | no | Version of the module's own payload shape. |
| `configuration_version` | no | Version of the module's configuration content. |
| `owner` | no | Team or system responsible for the module. |
| `status` | no | Free-text lifecycle status for the module document itself. |
| `dependencies` | no | List of `{module_id, version_constraint}`, documentation only -- never resolved or validated by any loader. |
| `payload` | module-type-dependent | Module-specific content. |

Routing a loaded module's `payload` into a canonical per-domain model (`AgentsConfig`,
`SourcesConfig`, `GraphConfig`, …) was `application/compatibility.py::LegacyCompatibilityAdapter`'s
job; that translation path was retired in CFG-1 (see below) because no process ever constructed it.

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

- `platform/` — one file per manifest entry, named after the module (`system_store.yaml`, read
  directly by `Settings.system_store_manifest_path` rather than through `manifest.yaml` -- see
  above). `agents/`, `workflows/`, `sync/`, `sources/`, `mappings/`, `graph/` are gone (CFG-5b);
  see "Removed as dead" below.
- `dynamic_knowledge/` — Dynamic Knowledge schemas. The active one is read directly by
  `Settings.dynamic_knowledge_schema_path` (default `active-schema.return-order.yaml`), the same
  direct-loader shape `platform/system_store.yaml` uses -- **not** through `manifest.yaml`, whose
  one `GRAPH` entry (`graph.order_discovery`) this lease deleted (CFG-5b; nothing read it, see
  "Removed as dead" below). A file in this directory that `dynamic_knowledge_schema_path` does not
  name is never loaded, including files like `active-schema.example.yaml` that exist purely as
  authoring references.
- `data_platform/` — canonical mappings, graph projection, sources, and sync-pipeline
  configuration for the data platform surfaces that have not yet migrated onto the manifest model.
  Loader: `data_platform/graph/sandbox_runner.py` (`_DEFAULT_CONFIG_DIR = config/data_platform`,
  the `--config-dir` default of the sandbox validation tool) and `backend/tests/**`; no request-time
  path reads these files. `data_platform/graph/migrations/` is read by
  `configuration/cli/apply_neo4j_migrations.py`.
- `seed/` — local/dev seeding inputs. `e2e_seed_manifest.json` is read at import time by
  `operations/seed_manifest.py` (so it is loaded by the running backend, not only by tooling);
  `generation.yaml` is the `DEFAULT_CONFIG` of `backend/scripts/generate_seed_data.py`.
- `schema_registry.yaml`, `data_assets.yaml`, `dependency_simulation.yaml` — governance and
  dependency-simulation inputs consumed directly by `Settings`, independent of the manifest.

## Adding a business key

There is no longer a manifest module system to add an entry to (see above). A new
`RETURN_PLATFORM`-level key (an agent included) is a new key of the composed `returns/` directory
-- add or extend the relevant part file, update `returns/index.yaml` if it is a new part, add the
model field on `ReturnPlatformConfiguration` (`configuration/return_configuration.py`), and run the
configuration test suite (`backend/tests/configuration/`) against the real files in this directory.

## Removed as dead

**CFG-1:** `policies/` (four files), `live_validation/data_assets.sampling.yaml`,
`dynamic_knowledge/internal_manifests/` (four files) and `reasoning.yaml` were named by the target
design but had zero readers in `backend/src`. They were deleted in CFG-1 (decision D-CFG-1); the
rule each one described, and where the equivalent live rule is (or that none exists), is recorded
in `docs/configuration/DEFERRED_DESIGN.md`.

**CFG-5b (D-CFG-1):** the manifest-driven module system -- `configuration/application/loader.py`
(`ConfigurationLoader`), `agents/` (8 files), `workflows/return_session.yaml`,
`sync/order_partial.yaml`, `sync/order_full.yaml`, `sources/sales_inv.yaml`,
`mappings/sales_inv_order.yaml`, `graph/order_discovery.yaml`. The Agents editing API
(`configuration/api/agents.py`) was the loader's only remaining consumer, and it now reads and
writes the live `RETURN_PLATFORM.agents` section directly; the six non-agent entries were already
loaded by nothing -- confirmed by grep and recorded by the CFG-5 implementer
(`.plan/tracks/CFG.ledger.md`, step:05: "`AgentConfigurationService._packaged()` filters to
`module_type == "AGENT"` only, and nothing else reads the manifest"), re-confirmed here (`grep
-rn "workflow.return_session\|sync\.order_\|source\.sales_inv\|mapping\.sales_inv_order\|graph\.order_discovery" backend/src backend/tests` -- zero hits) before deletion. `manifest.yaml` is
kept, trimmed to its one remaining (already-inert) entry -- see the file's own header comment.

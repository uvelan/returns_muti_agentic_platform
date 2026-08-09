# Graph Schema Analyzer

Derives a graph schema from configured data sources, with a human in the loop.
Design doc §2.7 (module shape), §9.3 (API), §13.6 (sample classification).

## What exists today (C3.1 + C3.2)

- **C3.1** — the persistent, independent module: domain, ports, per-entity
  persistence, `module.py`, versionless `/api/graph-schema`.
- **C3.2** — composition-root wiring, bounded source discovery with classified
  sample retention, the six-block prompt framing, and the LangGraph
  analysis/clarification/proposal/validation loop.

Typed mutations, graph-only editing, and approval are **C3.3**. Their routes and
nodes are absent rather than stubbed, so neither the OpenAPI schema nor the graph
advertises something that does nothing.

## The reasoning loop

`reasoning/` compiles a LangGraph that thinks, clarifies, proposes, and
validates — and **stops at `READY_FOR_APPROVAL`**. It never builds, activates,
drains, retires, or emits source DDL. A test scans the whole package for those
operation names, because the realistic mistake is someone adding a convenient
`request_build()` call here rather than routing through `ApprovalService`.

Every loop is bounded by `limits.py`; exhausting a budget routes to
`NEEDS_HUMAN_REVIEW` with a reason rather than raising. An analysis needing a
human is a normal outcome, not an error.

State holds **no raw source samples** — only `source_snapshot_id` and
`source_schema_hash`. An analysis checkpoint may sit at rest for days, so putting
samples there would create a second, unclassified copy with a different retention
story from the one the operator was promised.

## Why the boundary is this strict

The analyzer imports **no other business module**. Everything outside it is a
Protocol in `ports/`, bound to a real implementation in `bootstrap/adapters/` —
the only place permitted to see both sides. There is deliberately no `adapters/`
package here.

Two architecture tests enforce it, and they are not decorative:
`tests/graph_schema_analyzer/test_independence.py` (this module specifically,
including the pre-consolidation packages the platform test cannot know about) and
`tests/platform/test_no_module_cross_imports.py` (which began covering this
package the moment `module.py` appeared — this is the codebase's first one).

`platform.*` is exempt: it is shared infrastructure, not another module. That is
why `persistence/` may talk to the system store directly.

## Layers

| Layer | Rule |
|---|---|
| `domain/` | Pure. No I/O, no ports, no framework — asserted by a test. Holds the invariants everything else may assume. |
| `ports/` | The entire outward surface. Protocols only. |
| `persistence/` | One repository per entity family, each with its own write discipline. |
| `api/` | Versionless `/api/graph-schema`. Wire models are separate from domain models. |
| `module.py` | Lifecycle + router. Owns no cross-module wiring. |

## Two invariants worth knowing before changing anything

**Snapshots are immutable and content-addressed.** `content_hash` is derived from
dataset *metadata only* — never from which rows happened to be sampled — so two
captures of an unchanged source produce the same address. Loading a snapshot
whose stored hash disagrees with its datasets raises `SnapshotIntegrityError`
rather than returning untrustworthy evidence.

**Samples are never persisted unclassified** (§13.6). `SampleClassification` is
enforced in `SourceSchemaSnapshot`'s constructor, not in the repository, so a
snapshot that misrepresents how its samples were handled is impossible to *hold*,
not merely impossible to save:

| Classification | Requires |
|---|---|
| `NONE` | no `samples_ref`, no expiry — samples were used transiently and never written |
| `REDACTED` | a `samples_ref`; samples passed the platform redactor first. The default when sampling is on. |
| `ENCRYPTED` | a `samples_ref` **and** a mandatory `sample_expires_at`. Raw samples may never be retained indefinitely. |

`source_samples` is declared `encrypted: true` in the manifest, so the store layer
itself refuses a plaintext write to it.

## Wiring status

`main.py` bootstraps a `SystemStore` (re-introduced into the FastAPI process,
which Commit 3 had removed once the order agent stopped needing one), builds the
analyzer's persistence onto `app.state`, and mounts this router — so
`/api/graph-schema` is live. Failure to bootstrap degrades to an explicit
`INITIALIZATION_FAILED`/`UNAVAILABLE` state and a 503 from the routes rather than
blocking application startup: the analyzer is an operator tool, and the return
flow must not stop serving because schema analysis cannot persist.

`module.py` is complete and satisfies the platform `ModuleRuntime` contract, but
the router is currently mounted conventionally in `create_app` rather than through
module activation — `bootstrap/lifespan.py`'s own docstring places router mounting
in "steps 13–15 … supplied by the caller", and mounting during lifespan would
mutate `app.routes` after the OpenAPI schema is built. Activating the module
through `module_ids` is deferred until the kernel owns mounting.

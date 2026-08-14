# Graph Schema Analyzer

**Route** `/graph-schema` · **Capability** `graph_schema.draft.read` ·
**Component** `frontend/src/domains/graph-schema/GraphSchemaPage.tsx`

## Purpose

Turn source collections into the graph the copilot searches.

## Draft-scoped tabs, not domain sections

The domain registers **no sections**. That is deliberate: the analyzer's tabs —
Validation, Versions, Mapping, Shape, Diff — belong to a **selected draft**, not
to the domain. A rail entry for "Validation" with no draft selected would route
nowhere.

The Stitch design kit's own rail (Sources, Context, Schema, Validation, Indexes,
Sync) is the flow those screens will introduce; it goes into `registry.ts` when
they land, and not before. Inventing navigation ahead of the screens is how a
shell starts lying about what exists.

## UI regions

**Analysis list** — analyses in progress and complete.

**Analysis detail**:

- which sources were read, and the bounded sample taken from each;
- **clarification questions** the analyzer raised where the source shape is
  ambiguous, and their answers;
- the snapshot of what it observed.

**Draft detail** — for a draft produced from an analysis:

- the proposed schema shape;
- **mutations** applied by hand on top of the proposal;
- **revisions**, and the diff between any two;
- **validation** results;
- the approve / reject / publish controls.

**Contextual rail** — analysis and draft facts.

## Lifecycle, scope, and cutover

```text
analysis        bounded, masked reads of the configured sources
   │            clarifications where the shape is ambiguous
   ▼
draft           revisable. mutations · revisions · diff · validate · shape
   │
   ▼
approval        a human decision, through ProposalKernel
   │            (visible in /approvals as well as here)
   ▼
publish         an immutable schema release
   │
   ▼
activation      classified, then executed
```

### Scope

An analysis reads only the sources it was given, and only through the
`source_port`. **It never writes to a source.** Samples are masked before they
leave the read and before they reach the AI port.

A masker carries a salt for the lifetime of **one analysis**: the same customer id
tokenizes identically across every object in that analysis — otherwise the joins
the analyzer exists to find become invisible — and differently in the next one, so
tokens carry no meaning between analyses.

### Cutover

Activation is **not** a pointer flip. Read the migration plan first:

```http
GET /api/schema-releases/{release_id}/migration-plan
```

| Classification | Meaning | Strategy |
|---|---|---|
| `ADDITIVE` | New labels, edges or properties; nothing existing changes | `BACKFILL` the affected sources |
| `COMPATIBLE` | Identities stand, but a mapping moved — a property's path, type or cardinality | `AFFECTED_SCOPE_RESYNC` of those sources |
| `DESTRUCTIVE` | Identity changed, or something withdrawn a merge can never take back | `FULL_REBUILD` via a generation cutover |

Every non-trivial verdict carries its **reasons**, including the resync tier.
"Rebuild" without "why" is not reviewable, and this screen shows the reasons.

Activation used to be a pointer flip in the dark: an operator could move from a
release whose `Order` matched on `order_id` to one matching on `salesInvId` and
learn about it from the first associate who could not find an order. The migration
plan is the answer that flip has to give first.

See [`../architecture/graph-generations.md`](../architecture/graph-generations.md).

## Actions

| Action | API | Side effects | Reversible |
|---|---|---|---|
| Start an analysis | `POST /api/graph-schema/analyses` | Bounded, masked source reads | Abandon it |
| Answer a clarification | `POST /api/graph-schema/analyses/{id}/clarifications/{cid}/answer` | Refines the proposal | No |
| Abandon an analysis | `POST /api/graph-schema/analyses/{id}/abandon` | Terminal | No |
| Create a draft | `POST /api/graph-schema/analyses/{id}/drafts` | none beyond the draft | Yes |
| Mutate a draft | `POST /api/graph-schema/drafts/{id}/mutations` | New revision | Yes — revisions are kept |
| Validate | `POST /api/graph-schema/drafts/{id}/validate` | none | Yes |
| Re-analyze | `POST /api/graph-schema/drafts/{id}/reanalysis` | Fresh source reads | Yes |
| Approve / Reject | `POST .../approve`, `.../reject` | Records the decision | No |
| **Publish** | `POST /api/graph-schema/drafts/{id}/publish` | **Immutable schema release** | No |
| **Activate** | `POST /api/schema-releases/{id}/activate` | **Runs the migration strategy. May rebuild the graph into a new generation.** | Forward-only: activate a different release |

Activation is the one that moves data. `DESTRUCTIVE` activation builds a
replacement generation, validates it, swaps the `ActiveRuntimeSnapshot`, drains
readers on the old generation and retires it. **The active generation is never
dropped before its replacement validates** — a failed candidate is marked `FAILED`,
the compare-and-swap never runs, and the current generation keeps serving.

## Backend APIs consumed

The full 17-path `/api/graph-schema/*` surface plus `/api/schema-releases/*` —
listed in [`../architecture/graph-analyzer.md`](../architecture/graph-analyzer.md).

`frontend/src/api/graphSchema.contract.test.ts` asserts the client against the
published contract, so a backend shape change breaks a test rather than a screen.

## Live-state behaviour

Polled. An analysis is a long-running server-side job; the screen refetches its
status and renders progress from server fields only. Nothing advances on a timer.

## Loading, error and empty states

| State | Renders | Distinguished from broken by |
|---|---|---|
| No analyses | "No analyses" | Explicit |
| Analysis running | Server-reported stage | Never a timed progress bar |
| Clarification pending | The question, awaiting an answer | This is a **required input**, not a stall |
| Validation failed | The validator's own findings, per element | Not a generic failure |
| No migration plan yet | Explicit "not computed" | Distinct from "no changes" |
| Empty diff | States the revisions agree | Not "diff unavailable" |
| Load failure | Error panel with correlation id | |

## Persistence and data source

Analyses, drafts, revisions, samples and snapshots live in the platform system
store (**Platform MongoDB**), through `system_store_port`.

Published schema releases are immutable. `ActiveSchema` is the one compiled form;
nothing here is a second graph-schema representation.

Sources are read **read-only**, through `source_port`.

## Audit effects

Every decision is recorded through `audit_port`. Approvals additionally appear in
[`/approvals`](approvals.md) because `ProposalKernel` is one inbox.

Activation records the migration classification, its reasons and the strategy run.

Graph migrations are checksum-tracked in `ConfigurationMigration` nodes.

## Configuration dependencies

| Family | Effect | Restart |
|---|---|---|
| `sources` and `data_assets` | Which sources an analysis may read | Hot |
| Masking / retention policy | What is sampled and how it is tokenized. **Host-supplied through `masking_port`** — a second application composing this analyzer brings its own | Composition-level |
| `AI_GATEWAY` routes | Which model reasons over the samples | Hot |
| `graph` constraints/indexes | What a schema requires, derived by `graph/constraints.py` | Applied at activation |

## Known constraints

- The Stitch rail flow (Sources → Context → Schema → Validation → Indexes → Sync)
  is not yet implemented, so the domain has no sections.
- No visual graph explorer. The Data Console's graph explorer was deleted with no
  canonical equivalent — a deliberate decision, not an oversight.
- `INCREMENTAL` still exists in the migration-strategy enum so older recorded
  plans deserialize; nothing produces it any more.

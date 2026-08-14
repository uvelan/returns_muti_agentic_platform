# Archived documentation

**Nothing in this directory describes how the platform works today.**

Everything here is a plan, a status report, a review verdict or a design prompt
that was true when it was written and has since been overtaken. It is kept
because it is the record of how a decision was reached, not because it is a
description of the running system. When an archived document and a current
document disagree, the current document wins without argument.

Archived on 2026-08-14, at commit `dcbb7dc` on `refactor/unified-return-platform`.

## Why this directory exists

Twenty-eight planning and status documents sat in `docs/` beside the four that
describe the current platform, with no marking to say which was which. A reader
opening `docs/ORDER_DISCOVERY_GRAPH_FIRST_IMPLEMENTATION_PLAN.md` had no way to
learn that four later plans had replaced it, and several of them contradicted
each other about the same subsystem. That is the failure the audit recorded as
DOC-17: superseded plans standing beside current documentation as competing
truth.

Deleting them would have destroyed the reasoning; leaving them in place kept
them dangerous. They are moved instead, with the supersession named.

## What is current

| Subject | Current document |
|---|---|
| Where the platform is going | [`docs/UNIFIED_RETURN_PLATFORM_TARGET_DESIGN.md`](../UNIFIED_RETURN_PLATFORM_TARGET_DESIGN.md) |
| The plan being executed | [`docs/UNIFIED_RETURN_PLATFORM_IMPLEMENTATION_PLAN.md`](../UNIFIED_RETURN_PLATFORM_IMPLEMENTATION_PLAN.md) |
| Where execution stands | [`docs/UNIFIED_RETURN_PLATFORM_EXECUTION_STATE.md`](../UNIFIED_RETURN_PLATFORM_EXECUTION_STATE.md) |
| What was found wrong, with evidence | [`docs/UNIFIED_RETURNS_PLATFORM_DEEP_AUDIT_0615921.md`](../UNIFIED_RETURNS_PLATFORM_DEEP_AUDIT_0615921.md) |
| How the platform runs, screen by screen and flow by flow | [`docs/README.md`](../README.md) |

## Supersession record

### `order-discovery-plans/`

Eight documents planning Order Discovery across five successive designs: source
lookup, then graph-first, then context-driven configurable graph, then the
"final" configurable graph plan, then its V2. Each replaced its predecessor and
none said so.

Superseded by the shipped implementation and its documentation:

- [`docs/architecture/order-discovery.md`](../architecture/order-discovery.md) —
  what the discovery agent actually does.
- [`docs/architecture/identification-fields.md`](../architecture/identification-fields.md) —
  the runtime field catalogue that replaced the hardcoded anchor list these
  plans all assumed.
- [`docs/optimization/order-discovery-search.md`](../optimization/order-discovery-search.md) —
  the complete-corpus search invariant.

Specifically obsolete claims inside them: that a bounded `difflib` probe is
needed because Neo4j cannot match approximately (it can, through the
`customer_name_search_v2` full-text index); that colour and ZIP require code
changes (they are ordinary configured fields); and that strong anchors are a
fixed Python tuple (they are `discovery.identification_fields`, runtime
configuration).

| File | Was |
|---|---|
| `FINAL_ORDER_DISCOVERY_CONFIGURABLE_GRAPH_IMPLEMENTATION_PLAN.md` | Plan iteration 4 |
| `FINAL_ORDER_DISCOVERY_CONFIGURABLE_GRAPH_IMPLEMENTATION_PLAN_V2.md` | Plan iteration 5 |
| `ORDER_DISCOVERY_CANONICAL_ORDER_SYNC_IMPLEMENTATION_PLAN.md` | Sync half of iteration 3 |
| `ORDER_DISCOVERY_CONTEXT_DRIVEN_CONFIGURABLE_GRAPH_IMPLEMENTATION_PLAN.md` | Plan iteration 3 |
| `ORDER_DISCOVERY_FIELD_CORRECTIONS_AND_STRONG_ANCHORS.md` | Field corrections against iteration 2 |
| `ORDER_DISCOVERY_GRAPH_FIRST_IMPLEMENTATION_PLAN.md` | Plan iteration 2 |
| `ORDER_DISCOVERY_ORDER_ID_AND_ANCHOR_FINDINGS.md` | Findings behind iteration 2 |
| `ORDER_ANALYSIS_MANUAL_MAPPING_RECONCILIATION.md` | Manual source-mapping reconciliation, pre-analyzer |

### `stage-plans/`

The Stage 4L / 4M / 4N delivery sequence, the Ferguson implementation plan, the
graph-schema design-agent plan, the implementation-plan status tracker, and two
full-codebase review reports.

Superseded by the unified plan and by
[`docs/UNIFIED_RETURNS_PLATFORM_DEEP_AUDIT_0615921.md`](../UNIFIED_RETURNS_PLATFORM_DEEP_AUDIT_0615921.md),
which re-derived the findings against a single commit rather than across four
stages. The two Stage 4M/4N runbooks are superseded by
[`docs/operations/`](../operations/) — the dependency-simulator and AI-simulator
walkthroughs they describe still exist, but their startup, reset and
troubleshooting steps were written before the current launchers.

### `design-prompts/`

The two Stitch UI generation prompts. Superseded by the shipped screens and by
[`docs/screens/`](../screens/), which documents what was actually built rather
than what was requested. Several screens deliberately diverged: Data Sources
became its own domain instead of a Configuration tab, and Approvals is one queue
across proposal types rather than one queue per type.

### `reviews/`

Per-stage status documents and review verdicts from Stage 3b through 3h. These
were point-in-time assessments and are superseded by
[`docs/UNIFIED_RETURN_PLATFORM_EXECUTION_STATE.md`](../UNIFIED_RETURN_PLATFORM_EXECUTION_STATE.md).
Their `README.md` described a review process that is no longer run.

### `consolidation/`

`baseline-inventory.md` — the pre-consolidation inventory taken at
`c3cdd354fdef93583c2b67da219701e76489a221` on `feat/v2-order-discovery-integration`.
Superseded by the repository itself: the V1 console, the V2 shell and the Data
Console it inventories have all been deleted. Kept because it is the only record
of what those trees contained.

## What was deliberately *not* archived

- **`docs/evidence/`** — validation receipts and gate outputs. These are
  evidence of what ran at a point in time and are never "superseded"; a receipt
  does not stop being a record of its run.
- **`docs/execution-context/`** and **`docs/implementation/`** — the multi-agent
  execution process. They describe how work is carried out, not how the platform
  behaves, so they do not compete with the current documentation.
- **`docs/CONFIGURATION_RELEASE_LIFECYCLE_DECISION.md`** — a decision record.
  Decision records document a choice and its alternatives at the moment it was
  made; superseding the choice does not falsify the record.
- **`docs/SEED_DATA_GENERATION.md`** and
  **`docs/code_quality/LINUX_LIVE_VALIDATION_RUNBOOK.md`** — operational
  procedure still in use.

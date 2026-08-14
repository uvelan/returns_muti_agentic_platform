# Graph Schema Analyzer

**Current as of 2026-08-14, commit `dcbb7dc`.**

The analyzer reads source systems, proposes a graph schema, takes a human
through validation and approval, and hands an activated schema to the sync
service. It is deliberately **independent of the returns business** — it knows
nothing about cases, RMAs, bays or shipments.

## Business independence

The core imports **no** returns business module, no UI, no workflow, no case
service, and hardcodes **no** project source name. This is enforced statically by
`tests/graph_schema_analyzer/test_independence.py` and
`tests/platform/test_no_module_cross_imports.py`, not by convention.

The point is not ceremony. It is that the analyzer can be reasoned about, tested
and changed without loading the AI gateway or the graph module at all — and that
a future change to either cannot reach in here except through a named contract.

## Host composability

`composition.py` is the stated composition contract: everything an application
must supply to run this analyzer over its own data, named once, as ports.

| Port | Supplies |
|---|---|
| `source_port` | What objects exist, and bounded reads of them |
| `graph_target_port` | Where a proposed schema would be applied |
| `ai_port` | The reasoning gateway |
| `audit_port` | Where decisions are recorded |
| `system_store_port` | Drafts, sessions, samples, snapshots |
| `masking_port` — `SampleMaskingPort` / `SampleMaskerFactory` | How sampled values are tokenized |
| `masking_port` — `PayloadRedactionPort` / `RedactionPolicyFactory` | Which fields may be kept at all |

The last two were the gap. The application layer imported
`platform.redaction.SampleMasker` and `platform.redaction.AllowlistRedactor`
concretely and **constructed** them in four places, so a host could replace the
other five ports and still be handed this platform's opinion about what is
sensitive.

Both are **policy, not mechanism** — exactly the kind of thing a second
application has to decide for itself. What counts as sensitive, how a value is
tokenized, and which fields may be retained at all are answers that belong to
whoever is running the analyzer over their own data.

### Why a factory as well as an instance

A masker carries a salt for the lifetime of **one analysis**, deliberately:

- the same customer id must tokenize to the same value across every object read
  in that analysis, or the joins the analyzer exists to find become invisible;
- it must tokenize *differently* in the next analysis, so tokens carry no meaning
  between them.

A port that only handed over one instance would either leak salt across analyses
or make the lifetime the caller's problem. The factory preserves that semantic
across a host boundary.

### Defaults

`default_sample_masker` and `default_redaction_policy` bind this host's
implementations, and they are the **only** import of `platform.redaction` left
below the composition root. An application that wants different behaviour passes
its own factories and never loads them.

That is the whole difference between "the analyzer happens to work here" and "the
analyzer can be composed elsewhere".

### What stays host-owned

`api/` (FastAPI routers, `security` capabilities), `persistence/` (bound to this
platform's system store) and `module.py` (this platform's module lifecycle).
Those **are** a composition root, and a second application writes its own. The
finding was never that they exist — it was that nothing below them was
substitutable.

### No `adapters/` package

There is deliberately no `adapters/` package in this module. Binding these ports
to real implementations (`configuration.sources.registry`, the AI gateway, the
graph lifecycle) happens in `bootstrap/adapters/`, the only package allowed to
see both sides.

## Sources are strictly read-only

The analyzer **inspects** source systems. It never writes to them, and the
connectors it is given are read-only by code, not by configuration. Graph
configuration may narrow access; it cannot broaden it. See
[`security-boundaries.md`](security-boundaries.md).

Sampled values are masked before they leave the source read and before they reach
the AI port.

## Lifecycle

```text
analysis           reads sources, samples, proposes
  │                clarification questions where the shape is ambiguous
  ▼
draft              a proposed schema, revisable
  │                mutations · revisions · diff · validate · shape
  ▼
approval           a human decision through ProposalKernel
  │
  ▼
publish            an immutable schema release
  │
  ▼
activation         classified ADDITIVE / COMPATIBLE / DESTRUCTIVE,
                   executed as BACKFILL / AFFECTED_SCOPE_RESYNC / FULL_REBUILD
```

Activation is not a pointer flip. `GET /api/schema-releases/{id}/migration-plan`
gives the classification and its reasons before you activate. See
[`graph-generations.md`](graph-generations.md).

## API surface

| Method | Path |
|---|---|
| `GET`, `POST` | `/api/graph-schema/analyses` |
| `GET` | `/api/graph-schema/analyses/{analysis_id}` |
| `POST` | `/api/graph-schema/analyses/{analysis_id}/abandon` |
| `GET` | `/api/graph-schema/analyses/{analysis_id}/clarifications` |
| `POST` | `/api/graph-schema/analyses/{analysis_id}/clarifications/{clarification_id}/answer` |
| `POST` | `/api/graph-schema/analyses/{analysis_id}/drafts` |
| `GET` | `/api/graph-schema/analyses/{analysis_id}/snapshot` |
| `GET` | `/api/graph-schema/drafts/{draft_id}` |
| `POST` | `/api/graph-schema/drafts/{draft_id}/mutations` |
| `GET` | `/api/graph-schema/drafts/{draft_id}/revisions` |
| `GET` | `/api/graph-schema/drafts/{draft_id}/revisions/{sequence}/diff` |
| `GET` | `/api/graph-schema/drafts/{draft_id}/shape` |
| `POST` | `/api/graph-schema/drafts/{draft_id}/validate` |
| `POST` | `/api/graph-schema/drafts/{draft_id}/reanalysis` |
| `POST` | `/api/graph-schema/drafts/{draft_id}/approve` |
| `POST` | `/api/graph-schema/drafts/{draft_id}/reject` |
| `POST` | `/api/graph-schema/drafts/{draft_id}/publish` |

## AI use

The analyzer reaches models through the `ai_port`, which the host binds to
`FinalDispatcher`. It therefore inherits interception, recursive redaction,
pricing and telemetry without knowing any of them exist — which is the whole
argument for one dispatch boundary. See [`ai-dispatch.md`](ai-dispatch.md).

## Related

- [`../screens/graph-schema-studio.md`](../screens/graph-schema-studio.md)
- [`../screens/approvals.md`](../screens/approvals.md) — where a draft decision is made
- `backend/src/return_platform/graph_schema_analyzer/README.md` — module-local detail

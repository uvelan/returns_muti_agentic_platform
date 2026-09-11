# Deferred configuration design

D-CFG-1 (CFG-1): the files below are named by the target design's configuration
tree and parse cleanly, but nothing in `backend/src` loads them -- there is no
manifest module for `policy.*` reachable from a running process, no reader of
`live_validation/`, and no reader of `dynamic_knowledge/internal_manifests/`.
Each entry names the rule the file described and where that rule actually lives
today, if it lives anywhere. Deleted in this lease; this document is the record
of intent so the rule isn't rediscovered as a mystery later.

## `backend/config/policies/candidate_scoring.yaml`

Described the candidate-ranking thresholds for Order Discovery: a
`high_confidence` cutoff, an `ambiguity_gap`, whether an exact anchor is
required for auto-ranking, and per-evidence-type anchor weights
(`FULL_ORDER_ID`, `TRACKING_NUMBER`, `INVOICE_NUMBER`, `DELIVERY_TICKET`,
`CUSTOMER_PO`). The live equivalent is partial: the ambiguity gap is a real,
configured value -- `discovery.ambiguity_gap_millionths`, read in
`agents/order_discovery.py:82` -- but it comes from the `RETURN_PLATFORM`
release's `discovery` section, not this file, and the confidence cutoff and
per-evidence-type weights have no live counterpart; ranking order is decided
in code.

## `backend/config/policies/clarification.yaml`

Described clarification behavior: ask the smallest unresolved question,
suppress already-answered or inferable questions, and how the candidate cap
interacts with disclosure. The live equivalent is `clarification_policy`,
read in `agents/order_discovery.py:89` off the release's `clarification_policy`
section (which the CFG-0 audit found carries two fields beyond this file's
shape, `ordered_quantity` and `branch_location`). This file's specific knobs
(`suppress_inferable_questions`, `candidate_cap_behavior`) have no live
counterpart; the live section's field list is not a superset or subset of this
file's, it is a different shape entirely.

## `backend/config/policies/privacy.yaml`

Described a graph-property allowlist mode, a list of prohibited data
categories (`PAYMENT_CREDENTIAL`, `RAW_SECRET`, `FULL_TRANSCRIPT`,
`DOCUMENT_BYTES`, `UNRELATED_TELEMETRY`), preview redaction, and an
authorization-scope requirement. The live equivalent is not configuration at
all: redaction is code, in `ai/gateway/redaction.py`, which masks scalar
values under sensitive keys recursively (dicts, lists, and JSON-in-strings)
regardless of a switch. There is no live allowlist-mode toggle and no live
"prohibited categories" list matching this file's -- the sensitive-key list
this module walks against lives in `platform.redaction.sensitive_keys`, a
different module than this deleted file.

## `backend/config/policies/return_eligibility.yaml`

Described eligibility-evaluation preconditions: deterministic facts first, a
sealed discovery context required, a source revision required, prior-return
evidence required. The live equivalent is `return_eligibility_policy`, read
in `workflows/return_case_activities.py:1036` (and the surrounding gate) off
the release's `return_eligibility_policy` section. None of this file's four
named preconditions are individually toggleable in the live section; the live
gate is evaluated as configured policy data, not as these boolean switches.

## `backend/config/live_validation/data_assets.sampling.yaml`

A data-asset catalog naming two governance sampling fixtures (SQL Server and
MongoDB) with row caps and field redaction, for a live-validation sandbox
flow. The file's own header says it plainly: "never loaded by the production
FastAPI lifespan," referenced only by a live-sampling validator that builds
its own sandbox fixtures and tears them down -- and no such validator exists
in `backend/src`. There is no live equivalent; this was scaffolding for a
validator that was never built.

## `backend/config/dynamic_knowledge/internal_manifests/{mongodb,mssql,neo4j,postgresql}.yaml`

Each file names the internal platform-store schema (labels/collections/tables,
fields, indexes -- `internal_schema_state`, `configuration_releases`,
`conversation_state`, `outbox`, `audit_events`, and so on) for one of the four
backends `system_store.yaml`'s `payload.allowed_providers` lists, as if the
system store's internal structures were meant to be portable across any of
them. No code resolves `internal_manifests/` by provider or by any other key.
The live equivalent is `backend/config/platform/system_store.yaml`'s own
`payload.structures` block, which is provider-specific (Mongo physical names
and indexes) and is what `_SystemStoreConfigPayload` actually loads --
covering only the one provider (`MONGODB`) the running platform uses. The
per-backend portability these four files describe was never implemented.

## `backend/config/reasoning.yaml` and `load_reasoning_configuration`

Described LangGraph checkpoint-store settings for the reasoning platform:
schema version, enabled switch, checkpoint store selection (fixed to
`SYSTEM_STORE`), checkpoint encryption, retention (`terminalRetentionHours`,
`abandonAfterHours`, and a validator that forbids `activeRunsExpire: true`),
and a bounded-execution flag. `load_reasoning_configuration` (in
`platform/reasoning/configuration.py`) parsed and validated it, and was
re-exported from `platform/reasoning/__init__.py`, but nothing ever called
the loader. The live equivalent is partial and code-shaped rather than
config-shaped: `CheckpointRetentionPolicy` (`platform/reasoning/retention.py`)
takes `terminal_retention_hours` as a constructor argument from its caller,
not from this file, and the Mongo TTL indexes that actually expire terminal
checkpoints are declared directly in `config/platform/system_store.yaml`'s
`structures` (`expire_after_seconds: 0` on `reasoning_runs`,
`reasoning_action_receipts`, `order_discovery_query_evidence`). The
`enabled`/`checkpointEncryption`/`execution.bounded` switches this file
described have no live counterpart at all.

## `backend/config/dynamic_knowledge/active-schema.example.yaml` -- kept, not deleted

Named in this scope item as a candidate, but not removed: it is linked from
docs (`docs/UNIFIED_RETURN_PLATFORM_TARGET_DESIGN.md`,
`docs/UNIFIED_RETURN_PLATFORM_EXECUTION_STATE.md`) and is loaded directly by
`backend/tests/dynamic_knowledge/test_schema_and_fingerprint.py` as a real
fixture (`load_active_schema(...active-schema.example.yaml)`), which is
outside this lease's owned test paths. The brief's own condition for deleting
it -- "only if no doc links it" -- is not met, so it stays.

## `feature_flags` and `extensions` (D-CFG-2)

A different case from the files above: not unimplemented design, but two
blocks of switches that parsed, validated, and were never read by anything
that decided behavior from them.

`feature_flags` (`reusable_conversation_engine`, `order_discovery_copilot`,
`copilot_operations_console`, `graph_first_runtime_configuration`) had no
reader anywhere in `backend/src` -- confirmed by grep before deletion. There
is no live equivalent; each of the four capabilities it named either shipped
unconditionally (the reusable conversation engine, the copilot) or is gated
by something else entirely (`copilot.order_discovery_agent_id` being set, not
a boolean switch).

`extensions` (`document_artifact_metadata`, `ocr_processing`,
`image_processing`, `ncr_workflow`, `vendor_recovery_workflow`) had exactly
one reader: `api/return_agents.py`'s `/configuration` introspection endpoint
echoed `config.extensions.model_dump()` into its response JSON without ever
branching on any of the five values -- a display of dead configuration, not a
consumer of it. That one dict key was removed along with the block; the
endpoint's other fields are unchanged.

Both blocks are also gone from `frontend/src/domains/config/BusinessSection.tsx`'s
group list (they are no longer editable JSON sub-documents, because there is
no longer a model field for the document editor to patch) and from
`docs/configuration/families.md`'s classification table.

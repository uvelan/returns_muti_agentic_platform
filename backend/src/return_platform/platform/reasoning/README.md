# platform/reasoning

LangGraph durable reasoning foundation (Phase 5A / Wave B2). **Nothing consumes this
package yet** — Order Discovery's and the Graph Schema Analyzer's own reasoning graphs
(later phases) are the first real callers. This package holds no business reasoning of
its own: no Order Discovery graph, no Analyzer graph. Checkpoints are reconstructible
reasoning *position*, never the authoritative business record — canonical state stays
in `ReturnSession`/`Conversation`/`AnalysisSession`/`GraphSchemaDraft`/
`ConfigurationRelease` (future phases; none of these types exist yet either).

## What's here

| Module | Responsibility |
|---|---|
| `checkpoint.py` | `SystemStoreCheckpointSaver` — LangGraph's `BaseCheckpointSaver`, backed by SystemStore |
| `thread_ids.py` | `ReasoningThreadIdFactory` — one thread per reasoning *attempt*, not per conversation |
| `receipts.py` | `ReasoningActionReceipts` — idempotency state machine for side-effecting reasoning actions |
| `retention.py` | `CheckpointRetentionPolicy` — expiry keyed to terminal state, never creation time |
| `abandonment.py` | Sweeper + forced abandonment for idle runs, gated on five real preconditions |
| `resume_worker.py` | Delivers `reasoning_resume_commands` as real Temporal signals, at-least-once |
| `redaction.py` | `CheckpointRedactor` — rejects (never silently strips) disallowed checkpoint state keys |
| `observability.py` | Typed reasoning-run trace emission over `platform.audit.AuditSink` |
| `errors.py` | Typed exceptions this package raises |
| `configuration.py` | Loader for `config/reasoning.yaml` |

## Why LangGraph doesn't just use `InMemorySaver`

`InMemorySaver`/`MemorySaver` are forbidden outside unit tests (enforced by
`tests/reasoning/test_no_langchain_provider_packages.py` and
`test_langgraph_not_in_public_api.py`'s sibling checks) — they lose all reasoning state
on process restart, defeating the entire point of a *durable* reasoning platform.
`SystemStoreCheckpointSaver` resolves storage as SystemStore → logical structure →
configured physical collection (`reasoning_checkpoints`, `reasoning_checkpoint_writes`)
— never a hardcoded collection name, so a manifest change is the only way to repoint
storage. A real third-party alternative (`langgraph-checkpoint-mongodb`) was evaluated
and rejected: it pins `pymongo<4.17`, which would downgrade the `pymongo==4.17.0` this
platform's SystemStore work (Slice 3R) is built and extensively tested against.

Checkpoints are genuinely encrypted (`reasoning_checkpoints`/`reasoning_checkpoint_writes`
are declared `encrypted: true` in `config/platform/system_store.yaml`), via
`AesGcmEnvelopeEncryptor` (`platform/secrets/envelope.py`) — a real, working AES-256-GCM
implementation, interim pending a KMS-backed one (a separate, later concern; see that
module's docstring).

## Idempotency receipts are a state machine, not a cached value

LangGraph re-executes an interrupted node **from its beginning** on resume, so any side
effect before the interrupt runs again. A naive "record the result, return it on a hit"
design livelocks the interception path. See `receipts.py`'s module docstring for the
full state diagram; the short version: `STARTED` is written before the action, and a
resumed node resolves through `external_ref` rather than blindly re-acting.

## Retention and abandonment

Checkpoints for an active run (`RUNNING`/`INTERRUPTED`/`WAITING`) never expire — a Mongo
TTL index (`expire_after_seconds: 0` on `reasoning_runs`/`reasoning_action_receipts` in
`system_store.yaml`) ignores documents with a null `expires_at`. On a terminal
transition, `retention.py` stamps the same `expires_at` across the run, its checkpoints,
its writes, and its receipts, in one Mongo transaction.

Idle `INTERRUPTED`/`WAITING` runs would otherwise grow without bound (a user who never
answers a clarification). `abandonment.py`'s sweeper moves them to `ABANDONED` — but
only after checking five real preconditions (open receipt, open AI interception,
pending resume command, unresolved interrupt, active Temporal wait); a run failing one
is reported as blocked with the specific blocking reference, never silently retried or
silently left to grow. Forced abandonment (operator-triggered, or the sweeper) is one
Mongo transaction (state changes + a `reasoning_resume_commands` outbox row, together),
followed by separate, at-least-once, workflow-deduplicated Temporal signal delivery
(`resume_worker.py`) — a Temporal signal cannot join a Mongo transaction, so the
transaction durably records *intent to signal* and the worker delivers it afterward.
Verified against a real Temporal server and a real (throwaway, test-only) workflow:
signal delivery, workflow-side dedup on `command_id`, and the no-recipient case (a run
abandoned before any workflow was ever bound to it) all confirmed working end to end.

## Checkpoint content allowlist

`redaction.py`'s `CheckpointRedactor` **rejects** — never silently strips — any state
key outside a component's declared allowlist. Never allowlist: Vault secrets, database
passwords, API keys, credential-bearing connection strings, raw configuration
snapshots, raw unredacted source documents, large customer records, provider
authentication headers. Use references instead: `configuration_release_id`,
`graph_generation_id`, `source_snapshot_id`, `evidence_ref`, `query_execution_id`,
`candidate_id`, `schema_revision_id`.

## What's deliberately not here

- No business reasoning graph (Order Discovery's, the Analyzer's) — those are later
  phases, built *on* this foundation.
- No `langchain-openai`/`langchain-anthropic`/`langchain-google-genai` or any other
  provider integration package (D11.6) — a LangGraph node calling a provider directly
  would create a second AI routing path bypassing failover, rate limits, circuit
  breakers, interception, replay, safety, and metrics. Asserted at the dependency
  level by `tests/reasoning/test_no_langchain_provider_packages.py`.
- No real AI-Gateway/knowledge-graph wiring behind the receipt state machine's
  `external_ref` — that resolution logic belongs to whichever future phase's node
  actually calls the AI Gateway or a targeted sync, using this package's receipts to
  stay idempotent across resumes.

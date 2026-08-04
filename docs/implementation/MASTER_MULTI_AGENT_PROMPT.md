# Master Optimized Multi-Agent Implementation Prompt
## Dynamic Knowledge Graph and LLM-Driven Returns Platform

## 1. Repository target

```text
Repository:
https://github.com/uvelan/returns_muti_agentic_platform.git

Target branch:
feat/v2-order-discovery-integration

Primary environment:
Windows PowerShell

Primary IDE:
Antigravity IDE

Primary production-code writer:
Codex CLI
```

This document is the single implementation and execution source of truth.

Do not create a replacement implementation plan.  
Do not create ZIP files.  
Do not create unnecessary branches.  
Commit and push every completed, reviewed and validated step.  
Continue automatically until implementation is complete or a genuine blocker prevents safe progress.

---

# 2. Primary objective

Transform the current platform into a dynamic, knowledge-graph-first, independently operable multi-agent returns platform.

The completed platform must:

1. Own only internal platform schemas and models in code.
2. Treat every external business schema as dynamic runtime configuration.
3. Support MongoDB, MSSQL, PostgreSQL and Neo4j.
4. Create missing internal platform objects during startup.
5. Leave compatible existing internal objects unchanged.
6. Fail startup when required internal objects are incompatible.
7. Never hardcode external business tables, collections, fields, columns, graph labels, relationships or entity models in runtime logic.
8. Generate synchronization plans and Cypher from the approved active schema.
9. Calculate a deterministic graph-schema fingerprint.
10. Drop and fully rebuild the isolated business graph whenever the effective graph schema changes.
11. Preserve control-plane state during business graph rebuild.
12. Support knowledge-only operation with source databases offline.
13. Route every associate message through a configured standard reasoning model.
14. Remove regex business extraction, keyword sequencing and deterministic business fallback.
15. Use no lightweight model for Order Discovery reasoning.
16. Restrict every agent to configured business capabilities.
17. Enforce prompt-injection, permission, query-safety, hallucination and response guards.
18. Require immutable evidence for every graph-derived business fact.
19. Support targeted on-demand synchronization after a graph miss when a configured strong-anchor combination exists.
20. Project source records into the graph before agents use or display them.
21. Keep every business agent independently configured and isolated.
22. Use shared infrastructure without sharing agent-owned state.
23. Commit and push every completed step.
24. Continue execution without requesting confirmation between normal steps.

---

# 3. Authority and anti-hallucination rules

No agent may:

- Create another implementation plan.
- Rewrite or replace this architecture.
- Reduce requirements without repository evidence.
- Add speculative features unrelated to the assigned step.
- Claim a file exists without opening it.
- Claim a command ran without executing it.
- Claim a test passed without command output.
- Claim live validation from mocks.
- Claim repository-wide completion from focused tests.
- Fabricate commits, logs, API responses, coverage or runtime evidence.
- Stop after analysis when implementation can continue safely.

Use:

```text
UNKNOWN — repository evidence is unavailable
```

when a fact cannot be verified.

Use:

```text
BLOCKED — <exact blocker>
```

when safe progress cannot continue.

---

# 4. Latest-code and Git rules

Before every implementation step:

```powershell
git fetch --all --prune
git status
git branch --show-current
git rev-parse HEAD
git rev-parse origin/feat/v2-order-discovery-integration
git pull --ff-only origin feat/v2-order-discovery-integration
```

Required conditions:

- Current branch is `feat/v2-order-discovery-integration`.
- Local code is updated from the latest remote head.
- User-owned changes are identified and preserved.
- Starting commit is recorded in task context.

Do not silently merge, rebase, reset, force-push, discard files or hide changes using an undocumented stash.

Work directly on the target branch. Create another branch or worktree only when a real repository-safety problem requires isolation.

Every completed step must be:

1. Implemented.
2. Tested.
3. Independently reviewed when required by risk.
4. Independently validated.
5. Documented in agent-owned context.
6. Committed as one logical change.
7. Pushed.
8. Verified against the remote branch head.

Required completion sequence:

```powershell
git status
git diff --check
git diff --stat
git add <explicit-step-files>
git commit -m "<type>(<scope>): <completed result>"
git push origin feat/v2-order-discovery-integration
git fetch origin

$local = git rev-parse HEAD
$remote = git rev-parse origin/feat/v2-order-discovery-integration

if ($local -ne $remote) {
    throw "Local and remote branch heads do not match."
}

git status
```

Do not create ZIP files unless explicitly requested after completion.

---

# 5. Continuous execution and blockers

After each successful push:

1. Update the master execution context.
2. Identify newly unlocked tasks.
3. Select the next ready task.
4. Continue without asking for confirmation.

Do not stop because a task, review, validation or phase completed.

Valid blockers include:

- Repository access failure
- Git authentication or push failure
- Required credentials unavailable
- Required infrastructure inaccessible
- User-owned changes conflict with required work
- Unsafe branch divergence
- Repository corruption
- Irreversible migration cannot be validated safely
- Mandatory dependency unavailable
- Directly contradictory requirements
- Critical security decision requires unavailable information
- Three materially identical failures produce no new diagnostic signal

A normal test failure is not a blocker. Diagnose, repair and rerun.

---

# 6. Multi-agent execution model

Every production task follows separate ownership:

```text
Focused repository analysis
→ Production implementation
→ Independent code review
→ Security review when required
→ Independent validation
→ Correction loop when required
→ Commit and push
→ Context consolidation
```

The same agent must not:

- Implement and approve its own work.
- Implement and perform final validation.
- Review and silently modify production code.
- Validate and modify production code.
- Mark completion without evidence.

Only one implementation agent may write production code in the shared working tree at a time.

Parallel execution is allowed for non-writing analysis, review planning and test design when file ownership does not conflict.

---

# 7. Model and role mapping

| Role | Primary | Fallback |
|---|---|---|
| Orchestrator and critical architecture | Gemini 3.1 Pro | Sonnet 4.5 |
| Focused repository analysis | Gemini 3.6 Flash | Gemini 3.1 Pro |
| Production implementation | Codex CLI | Gemini 3.1 Pro |
| Data-platform implementation | Codex CLI | Gemini 3.1 Pro |
| AI-runtime implementation | Codex CLI | Gemini 3.1 Pro |
| Frontend implementation | Codex CLI | Gemini 3.6 Flash |
| Independent code review | Sonnet 4.5 | Gemini 3.1 Pro |
| Security review | Sonnet 4.5 | Gemini 3.1 Pro |
| Independent validation | Gemini 3.6 Flash | Gemini 3.5 Flash |
| Low-risk context maintenance | Gemini 3.5 Flash | Gemini 3.6 Flash |
| Commit and push | Codex CLI | none |

Do not run every model on every task. Follow `TASK_CLASSIFICATION_AND_MODEL_ROUTING.md`.

---

# 8. Token and tool efficiency

Agents must minimize token usage, tool calls and repeated work without reducing correctness.

Required behavior:

- Read existing contexts before searching the repository.
- Reuse verified evidence when relevant files and contracts are unchanged.
- Inspect assigned files first, then direct callers, related tests and public contracts.
- Avoid full repository scans except at baseline, phase integration, security-wide or final gates.
- Do not repeat passing tests without a relevant code, configuration or dependency change.
- Do not restate this prompt in agent contexts.
- Do not copy previous contexts.
- Do not use LLMs for deterministic schema, permission, checksum, idempotency or query validation.
- Use the smallest sufficient validation set during a task.
- Run full suites only at phase and final gates.
- Stop repeated command execution when no new diagnostic signal is produced.
- Keep handoffs concise and context-referenced.

---

# 9. Internal platform ownership

Code may own internal structures such as:

```text
internal_schema_state
configuration_releases
active_configuration
schema_versions
schema_activation
graph_projection_state
graph_sync_runs
graph_sync_checkpoints
graph_rebuild_leases
graph_rebuild_receipts
on_demand_sync_requests
on_demand_sync_receipts
conversation_state
conversation_messages
conversation_facts
candidate_sets
query_executions
query_evidence
agent_turns
idempotency_records
distributed_locks
outbox
worker_heartbeats
provider_health
audit_events
```

Code must not own fixed external business models such as Customer, Order, Product, Shipment or Return.

Business names may exist in configuration, tests, documentation and seed data, but must not control generic runtime branching.

Invalid:

```python
if entity_id == "customer":
    resolve_customer()
```

Valid:

```python
entity = active_schema.require_entity(action.entity_id)
```

---

# 10. Internal-store foundation

Support internal stores:

```text
MONGODB
MSSQL
POSTGRESQL
NEO4J
```

Implement a common adapter contract for:

- Schema inspection
- Missing-object creation
- Compatibility validation
- Transactions where supported
- Fenced distributed leases
- Configuration state
- Conversation state
- Idempotency
- Audit
- Synchronization state
- Agent state

Startup must:

1. Read bootstrap configuration.
2. Resolve the configured adapter.
3. Resolve secrets through secret references.
4. Load the engine-specific manifest.
5. Inspect existing objects.
6. Create missing objects.
7. Create missing required indexes and constraints.
8. Leave compatible objects unchanged.
9. Permit additional non-conflicting fields.
10. Fail on missing or incompatible required fields.
11. Load the approved active configuration.
12. Load graph-generation state.
13. Initialize enabled agents and connectors.

---

# 11. Dynamic external schema

External source configuration must define:

- Connector type
- Connection reference
- Physical source object
- Logical field identifiers
- Physical paths
- Types
- Keys
- Incremental cursor
- Search, filter and aggregation capabilities
- Distinct-value capability
- Permissions
- Masking
- Strong anchors
- Graph mappings
- Dependency rules

Runtime code must operate on logical descriptors.

Invalid:

```python
record.get("orderNumber")
```

Valid:

```python
path_resolver.resolve(record, compiled_field.physical_path)
```

---

# 12. Source connectors

Implement:

```text
MongoSourceConnector
MssqlSourceConnector
PostgresqlSourceConnector
Neo4jSourceConnector
```

Common operations:

```text
validate_connection
introspect_schema
estimate
full_scan
incremental_scan
targeted_read
checkpoint_read
```

Every connector must:

- Compile logical plans to safe native reads.
- Parameterize values.
- Enforce read-only access.
- Enforce timeouts and row limits.
- Return generic dynamic record batches.
- Support strong-anchor targeted reads.
- Prevent credential logging.
- Avoid unrestricted source reads.

The generic sync engine must use a connector registry, not engine-specific branching.

---

# 13. Dynamic mapping and graph projection

Required pipeline:

```text
Source connector
→ Dynamic records
→ Mapping compiler
→ Mapping executor
→ Graph projection plan
→ Safe Cypher compiler
→ Generic graph writer
```

Configuration defines:

- Node labels and keys
- Node properties
- Relationship types and directions
- Relationship keys and properties
- Constraints
- Indexes
- Transformations
- Dependencies
- Null behavior

On-demand synchronization must use the same pipeline as full and incremental synchronization.

The LLM must never generate executable Cypher.

---

# 14. Graph fingerprint and rebuild

Fingerprint all graph-affecting configuration:

- Source identities
- Physical source paths
- Logical fields and types
- Keys
- Transformations and versions
- Nodes, labels and properties
- Relationships
- Dependencies
- Constraints
- Indexes
- Null handling
- Projection compiler version

Exclude secrets, passwords, runtime timestamps and operational batch sizes.

Graph states:

```text
UNINITIALIZED
ACTIVE
SCHEMA_CHANGE_DETECTED
REBUILD_PENDING
REBUILDING
VALIDATING
FAILED
```

On fingerprint change:

1. Mark schema change.
2. Acquire a fenced global rebuild lease.
3. Block business graph reads.
4. Block incremental and on-demand synchronization.
5. Drop business graph nodes and relationships.
6. Remove obsolete business indexes and constraints.
7. Create configured constraints and indexes.
8. Perform full synchronization.
9. Validate graph integrity.
10. Create and activate a new graph generation.
11. Release the lease.

Use isolated control and business graph storage:

```text
platform_control
business_knowledge
```

Every turn, query, candidate set and evidence record must be pinned to the active configuration and graph generation.

---

# 15. Runtime modes

Support:

```text
KNOWLEDGE_ONLY
CONNECTED_READ
CONNECTED_SYNC
```

`KNOWLEDGE_ONLY` must work with source systems offline and disable scheduled and on-demand source synchronization.

---

# 16. LLM-driven Order Discovery Agent

Every associate message must invoke a configured standard reasoning model.

The model decides:

- Business capability
- Relevant configured entities
- Entity-resolution order
- Search and traversal strategy
- Aggregation and distinct-value requirements
- Clarification question
- Candidate ranking
- Strong-anchor sync request
- Discovery completion

Application code validates and executes structured actions.

Remove:

- Regex business extraction
- Keyword-only intent routing
- Hardcoded field parsing
- Fixed customer/product/order sequence
- Fixed clarification questions
- Fixed suggestions
- Direct source queries
- Lightweight reasoning models
- Static business fallback
- Rule-based business fallback
- Raw model-generated database queries
- Chain-of-thought persistence

Only configured `STANDARD_REASONING` models may serve Order Discovery turns.

If all eligible models fail, return a typed retryable error and do not generate a deterministic business response.

---

# 17. Generic tools and actions

Schema tools:

```text
get_schema_summary
search_schema
get_entity_definition
get_field_definition
get_relationship_paths
get_operation_capabilities
```

Knowledge and analysis tools:

```text
search_entities
filter_entities
traverse_relationships
semantic_search
aggregate
count_candidates
get_distinct_values
rank_candidates
find_disambiguating_fields
```

Conversation tools:

```text
save_user_fact
create_candidate_set
resolve_entity
confirm_candidate
complete_discovery
```

Synchronization tools:

```text
request_on_demand_sync
read_on_demand_sync_status
```

Do not create entity-specific tools such as `search_customers` or `find_order_by_number`.

The model may return only typed actions. Malformed output must be rejected, corrected within limits or failed explicitly.

---

# 18. Safeguards

Implement deterministic:

- Business Capability Guard
- Schema Guard
- Permission Guard
- Query Safety Guard
- Strong Anchor Guard
- Hallucination Guard
- Response Guard
- Structural prompt-injection controls

Treat user messages, graph values, source text, notes, tool results and prior model output as untrusted data.

Maintain separation between:

```text
SYSTEM POLICY
ACTIVE BUSINESS CONFIGURATION
SCHEMA METADATA
USER INPUT
GRAPH DATA
TOOL RESULTS
```

Never expose system prompts or allow graph values to define tools or policies.

Conversational agents use read-only graph credentials.

Permissions and masking come from active configuration. Do not hardcode sensitive-field bans.

---

# 19. Evidence and hallucination controls

Every graph-derived claim must reference immutable evidence.

Validate:

- Entity, field and relationship exist.
- Operation is configured.
- Permission is granted.
- Candidate belongs to the active immutable candidate set.
- Graph generation matches.
- Counts and values match query evidence.
- Negative claims match exact query scope.
- No stale or unauthorized evidence is used.

Supported response statement types:

```text
GRAPH_FACT
USER_PROVIDED_FACT
REASONED_SUGGESTION
CLARIFICATION_QUESTION
```

Unsupported free-form factual prose must not reach the UI.

Persist structured actions, decision summaries, tool references, evidence references, guard decisions and model metrics. Do not persist private chain-of-thought.

---

# 20. Conversation consistency

Every turn requires:

```text
conversationId
expectedConversationVersion
clientTurnId
idempotencyKey
messageDigest
```

Behavior:

- Same key and digest: replay stored result.
- Same key and changed digest: reject.
- Stale conversation version: reject.
- Concurrent turns: serialize or compare-and-set.
- Duplicate retries: do not duplicate messages or candidate sets.

Candidate sets must be immutable, scoped and graph-generation pinned.

---

# 21. On-demand strong-anchor synchronization

A graph miss alone must not trigger source access.

Allow targeted synchronization only when:

- Business capability permits it.
- Graph is active.
- Current-generation graph query produced no match.
- Model proposes a configured strong anchor.
- Deterministic guard validates fields, values, operators, permissions, scope, rate and query budget.
- Connector supports targeted reads.
- Equivalent synchronization is not already active or recently completed.

The model provides logical fields only and never knows physical tables, collections, columns, query languages or credentials.

Targeted synchronization must:

1. Compile a logical source read.
2. Retrieve only mapped fields and required dependencies.
3. Use durable idempotency and coalescing.
4. Project through the normal graph pipeline.
5. Persist a receipt.
6. Rerun the original graph query.
7. Return graph evidence to the model.
8. Never expose raw source records.

---

# 22. Independent business agents

Required bounded agents:

```text
Order Discovery Agent
Return Workflow Agent
Eligibility Agent
Fulfillment Agent
RMA/RGA Agent
Label and Tracking Agent
Bay Assignment Agent
Feedback Learning Agent
```

Each owns:

- Policy
- Model configuration
- Tool registry
- State namespace
- Versioned contracts
- Temporal task queue
- Rate and concurrency limits
- Circuit breaker and retries
- Audit
- Metrics
- Health
- Permissions

Agents communicate only through versioned APIs, commands, events, transactional outbox and Temporal workflows.

No agent may directly modify another agent's internal state.

---

# 23. Implementation phases

## Phase 0 — Baseline and execution control

- Capture branch, commit, working tree, versions, routes, agents, databases, infrastructure, tests and migrations.
- Create master execution context and execution policy.
- Inventory regex extraction, fixed flows, hardcoded schema, fixed Cypher, lightweight routing, fallbacks and cross-agent state.
- Commit and push each completed step.

## Phase 1 — Internal-store foundation

- Generic adapter contracts
- MongoDB adapter
- MSSQL adapter
- PostgreSQL adapter
- Neo4j control adapter
- Startup bootstrap
- Concurrent startup and compatibility validation

## Phase 2 — Dynamic configuration

- Immutable configuration releases
- Dynamic source descriptors
- Dynamic graph descriptors
- Configuration validation
- Active schema runtime and release pinning

## Phase 3 — Source connectors

- Generic connector registry
- MongoDB connector
- MSSQL connector
- PostgreSQL connector
- Neo4j connector
- Schema introspection producing draft proposals

## Phase 4 — Mapping and Cypher

- Dynamic record and path resolver
- Versioned transformation registry
- Mapping compiler
- Graph projection compiler
- Safe graph-write compiler
- Safe graph-read compiler

## Phase 5 — Graph lifecycle

- Canonical fingerprint
- Graph-generation state
- Fenced rebuild coordinator
- Business graph cleanup and schema recreation
- Full synchronization
- Graph validation and activation

## Phase 6 — Generic knowledge repository

- Typed logical query plans
- Immutable query evidence
- Query budgets
- Schema discovery tools
- Generic knowledge tools

## Phase 7 — Order Discovery Agent

- Standard reasoning model gateway
- Structured actions
- Conversation coordinator and tool loop
- Dynamic entity resolution
- Aggregation and distinct-value reasoning
- Immutable candidate sets
- Turn idempotency and concurrency

## Phase 8 — Safeguards

- Capability Guard
- Schema Guard
- Permission Guard
- Query Safety Guard
- Prompt-injection structural controls
- Hallucination Guard
- Response Guard
- Security review

## Phase 9 — On-demand synchronization

- Strong-anchor configuration
- Strong Anchor Guard
- Targeted logical read planner
- Dependency expansion
- Durable requests and coalescing
- Standard graph projection reuse
- Post-sync graph retry

## Phase 10 — API and frontend

- Versioned APIs
- Existing Copilot compatibility adapter invoking the new coordinator
- OpenAPI regeneration
- Frontend turn client
- Dynamic candidates and suggestions
- Agent and graph operational states
- Frontend lint, typecheck, tests, build, Playwright and accessibility

## Phase 11 — Remaining independent agents

Migrate Return Workflow, Eligibility, Fulfillment, RMA/RGA, Label and Tracking, Bay Assignment and Feedback Learning independently.

## Phase 12 — Legacy removal

Remove legacy extraction, routing, fixed flow, hardcoded schema, fixed Cypher, lightweight routing, fallbacks and obsolete compatibility only after replacement validation.

## Phase 13 — Full validation

Run full static, backend, frontend, infrastructure, concurrency and adversarial validation.

## Phase 14 — Final completion

Create and push `docs/execution-context/FINAL_IMPLEMENTATION_CONTEXT.md`.

Do not create a ZIP unless explicitly requested later.

---

# 24. Minimum typed error codes

```text
ORDER_AGENT_OUT_OF_SCOPE
ORDER_AGENT_LLM_FAILED
ORDER_AGENT_MODEL_OUTPUT_INVALID
ORDER_AGENT_RESPONSE_VALIDATION_FAILED
ACTIVE_SCHEMA_UNAVAILABLE
KNOWLEDGE_GRAPH_NOT_ACTIVE
KNOWLEDGE_GRAPH_QUERY_FAILED
GRAPH_GENERATION_CHANGED
ORDER_AGENT_QUERY_BUDGET_EXCEEDED
CONVERSATION_VERSION_CONFLICT
CANDIDATE_SET_STALE
ON_DEMAND_SYNC_STRONG_ANCHOR_REQUIRED
ON_DEMAND_SYNC_SOURCE_UNAVAILABLE
ON_DEMAND_SYNC_FAILED
ORDER_NOT_FOUND_AFTER_ON_DEMAND_SYNC
```

Response shape:

```json
{
  "code": "ERROR_CODE",
  "message": "Business-safe message",
  "retryable": true,
  "correlationId": "uuid"
}
```

Frontend logic must use `code`, not message text.

---

# 25. Step completion gate

A step is complete only when:

```text
Latest remote code fetched
Starting commit recorded
Required analysis completed
Implementation completed
Focused tests added
Focused tests passed
Independent review approved when required
Security review approved when required
Independent validation passed
Agent-owned contexts written
Step completion context written
One logical commit created
Commit pushed successfully
Local HEAD equals remote branch HEAD
Master execution context updated
No unsupported completion claim exists
```

Begin at Phase 0 against the latest remote branch and continue through all phases without requesting confirmation between normal steps.

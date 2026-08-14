# Security boundaries and the source read-only policy

**Current as of 2026-08-14, commit `dcbb7dc`.**

## Ownership and access

| System | Responsibility | Platform access |
|---|---|---|
| Platform MongoDB | Conversation state, cases, discovery locks, audits, configuration receipts, process adoption records, outbox, operational state | Read/write |
| **Source** MongoDB | Customer, order, shipment and product source records | **Read-only** |
| SQL Server — **source** objects | Source order/customer/product tables | **Read-only** |
| SQL Server — **platform-owned** objects | `dbo.return_requests`, `dbo.return_items`, `dbo.return_case`, `dbo.return_record`, `dbo.return_record_item`, `dbo.return_fulfillment`, `dbo.return_tracking`, `platform.bay_assignment`, `platform.bay_reservation`, `integration.return_support_ticket` | **Read/write** |
| Neo4j | Runtime configuration control plane, and the discovery/knowledge graph | Controlled read/write |
| Temporal | Workflow execution, retries, timers | Execution only |
| Valkey | Event streams and non-secret runtime coordination | Read/write |
| Vault | Database credentials, AI keys, tokens, certificates, validation fingerprint material | Path-scoped read/write |

### The distinction that was being conflated

This table previously said "SQL Server … Read-only" with no qualification, while
`sql_business_state.py` inserted into seven tables. That is not a contradiction
in the code; it was a wrong sentence in the documentation, and a wrong sentence
in a **security-boundary table** is worse than no sentence.

> **"Read-only" applies to source-system objects only.** The platform owns and
> writes its own return tables in the same server. The two must not be conflated:
> the read-only guarantee is a security boundary against source systems, not a
> claim that the platform never writes to SQL Server.

The boundary is real and it is enforced in code, not by convention:

- Source MongoDB and source SQL Server connectors are **permanently constrained
  to read-only access by code**.
- Graph configuration **may narrow** access; it **cannot broaden** it. An
  operator cannot configure a source into writability.
- Platform-owned tables are created by the platform's own migrations
  (`backend/src/return_platform/configuration/sql_migrations/`) and live in
  `dbo`, `platform` and `integration` schemas the platform declares.

### The bay-candidate lesson

`WarehousePlacementService` used to read bay candidates from
`SQLBusinessStateRepository.list_bay_candidates` on every call — a **direct
source read from an agent path**, which the policy forbids. It now reads
candidates from the graph, and a graph that cannot be reached does **not** fall
back to the SQL bypass:

> Silently reading the source the step removed would make the removal a comment.

That is the general rule. A read-only boundary that is bypassed on failure is not
a boundary.

## Data-source activation

A data source cannot be activated until the backend verifies:

- connector type;
- endpoint allowlist and DNS resolution;
- cloud metadata endpoint blocking;
- transport connectivity;
- authentication;
- a safe health query;
- required databases, collections, tables or indexes;
- the requested access mode against the **code-owned connector capability**;
- configuration checksum and exact Vault secret version.

A source endpoint must appear in `PLATFORM_DATA_SOURCE_ALLOWED_HOSTS`. Production
changes should use explicit hostnames or CIDR ranges rather than broad network
ranges.

## Secrets

Vault KV v2 is the exclusive runtime source for credential values. Neo4j stores
only versioned references.

**Secrets must never be stored in** Neo4j, MongoDB documents, Valkey, Temporal
payloads, frontend storage, logs, evidence files or AI traces. The frontend never
receives a secret value.

Vault writes use compare-and-swap versioning. If receipt persistence fails, the
staged write is rolled back without exposing the secret.

The local Vault bootstrap stores separate MongoDB connection references for host
and container execution — host processes resolve loopback DSNs, container
processes resolve Docker service DNS names. Published graph entries marked
`bootstrap_managed` cannot override these deployment-specific endpoints.

## Contact evidence and HMAC

Phone and email lookup evidence is stored in Neo4j as normalized,
**domain-separated HMAC-SHA256** values, keyed by a Vault-managed key. Raw contact
details are not projected.

**Rotating that key requires a complete customer graph reprojection before
phone/email lookup is re-enabled**, because existing evidence cannot be recomputed
in place. The key is intentionally non-recoverable from graph evidence — that is
the property that makes the evidence safe to store, and it is also why rotation
is expensive.

## Authorization

Capabilities, not roles, are the unit. `security/` holds the capability
definitions and the FastAPI dependencies that enforce them.

Frontend hiding is **presentation only** — a domain whose `requires` capability
the principal lacks is not shown, and the backend refuses regardless. A screen
that appears is not an authorization decision.

Tenant and principal ownership guards run on every case and conversation read.
`CandidateSet.validate_selection` re-binds a selection to conversation,
principal, tenant and graph generation before anything is written.

`GET /api/principal` reports the caller's capabilities. `GET /api/session`
reports the session.

## AI boundaries

- AI receives **redacted, bounded facts only**. Redaction is recursive and runs
  **before** the interception verdict.
- AI **cannot** choose candidates, change workflow state, generate database
  queries, or bypass confirmation.
- No business agent holds a raw provider client. Every external model request
  crosses one boundary — see [`ai-dispatch.md`](ai-dispatch.md).
- Fuzzy indexes contain **approved natural-language fields only**.
- A human answer to an intercepted request is reported as `MANUAL`, never as the
  replaced provider.
- Prompt-injection controls: an untrusted utterance may be *classified*; explicit
  identifiers always win over conflicting AI output.

## Governance

`ProposalKernel` is one inbox across proposal types, with a forbidden-key policy
(`platform/governance/key_policy.py`) that refuses configuration keys no proposal
may touch. All administrative actions are audited.

## Fail-closed behaviour

| Condition | Result |
|---|---|
| Configuration checksum mismatch | Refuse startup or activation |
| Stale configuration head revision | Configuration revision conflict |
| Vault unavailable **before** client creation | Fail the affected dependency initialization. **Never** fall back to `.env` credentials. |
| Vault temporarily unavailable **with** initialized client pools | Continue bounded use of already-established clients |
| `AI_GATEWAY` or `DEPENDENCY_SIMULATION` absent in production/staging | Fail closed |
| Stale candidate card | Reject on candidate-set id, expiry, or conversation version |
| Duplicate message | Return the prior idempotent result rather than applying it twice |
| Dependency simulation requested in production | Refused — "External dependency simulation is forbidden in production." |

## Related

- [`configuration-adoption.md`](configuration-adoption.md)
- [`../screens/data-sources.md`](../screens/data-sources.md)
- [`../operations/troubleshooting.md`](../operations/troubleshooting.md)

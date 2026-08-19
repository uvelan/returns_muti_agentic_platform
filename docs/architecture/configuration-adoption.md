# Runtime configuration and worker adoption

**Current as of 2026-08-14, commit `dcbb7dc`.**

## The source of truth

**The database is the runtime source of truth.** YAML under `backend/config/` is
bootstrap/default input only and is **never rewritten at runtime**. Packaged YAML
immutability is a preserved property, not an implementation detail.

Neo4j holds the versioned configuration control plane. MongoDB retains the
digest-addressed runtime snapshot as audit evidence; it is not an editable
configuration authority.

## Release lifecycle

```text
DRAFT → VALIDATED → RELEASED → SUPERSEDED → ARCHIVED
```

Published releases are immutable. Publication requires the **expected head
revision**, so two administrators cannot activate two releases from the same
starting point.

Every conversation and every case pins:

- configuration release id
- configuration head revision
- configuration checksum
- configuration source

## Startup sequence

```text
load version-controlled baseline schema
  → read credentials from the process environment
  → connect to Neo4j
  → load the active ConfigurationHead release
  → verify release checksum
  → validate the complete configuration model
  → create immutable process snapshot
  → initialize dependency clients
```

A checksum mismatch **refuses startup or activation**. It does not warn.

## Hot adoption (C5)

Every long-running API and worker process runs a configuration reconciler. When
the active release changes, the reconciler:

1. validates the new release;
2. constructs a complete immutable snapshot;
3. **atomically swaps** process-local configuration;
4. reports its adopted release id and head revision.

A single replica-scoped epoch swap is what makes a change visible atomically — no
request ever observes two modules on two different releases. Every request holds
a uniquely-identified `EpochLease`, not a bare count, so releasing one request's
lease can never be mistaken for releasing another's.

New work uses the new active release. **Existing cases continue on their pinned
release** unless a configuration family is explicitly documented as
non-case-pinned. A workflow reads its timings once at start and keeps them for
its lifetime; an in-flight return must not have its deadline moved underneath it.

Infrastructure endpoint changes are **restart-required and fail closed**.

## `ACTIVATED` is not `LIVE`

This is the distinction the adoption report exists to make. A release is
activated the moment an administrator publishes it. It is **live** only when
every required process class is actually running it.

```http
GET /api/config/adoption
```

| Status | Meaning |
|---|---|
| `LIVE` | Every required class has at least one live instance, and **all** of them report the activated release id **and** head revision. |
| `ACTIVATING` | The release is activated and at least one required class has not adopted it — either still on the previous one, or no live instance of it is reporting at all. |
| `NO_ACTIVE_RELEASE` | Nothing is activated, so there is nothing to adopt. |

Both halves of the identity are checked. A process reporting the right release id
at an older head revision has not adopted.

### The required process classes

| Class | Process |
|---|---|
| `api` | The FastAPI process |
| `return-workflow-worker` | `ReturnCaseWorkflow` and its activities |
| `order-discovery-worker` | `OrderDiscoveryWorkflow` turns |
| `return-orchestrator` | The session-scoped return orchestrator |
| `outbox-publisher` | The transactional outbox |
| `integration-outbox-worker` | External dependency commands |

These are the identifiers the processes already publish — the same strings their
heartbeats use — not new names invented for adoption.

**The API process is in the set** because it holds its own snapshot and serves
reads from it. A release adopted by every worker and not by the API is exactly as
split as the reverse.

**`data-job-worker` is deliberately absent.** `compose.yaml` deployed it, but its
launcher (`scripts/run_data_job_worker.py`, since deleted) imported
`return_platform.data_console`, which does not exist in this repository, so the
container could never start. Listing a
class that can never report would make every release permanently not-live and
turn a real signal into one operators learn to ignore. (The dead worker and its
compose entry were subsequently removed; the note stands as the rule for adding a
class here.)

### Adoption records vs heartbeats

Adoption lives in `runtime_process_adoptions`, one document per **live process
instance**, `_id = "<class>:<instance>"`.

Deliberately not the `worker_heartbeats` document, which is keyed by class alone
and therefore cannot hold two replicas. Readiness asks "is this class up", which
one row per class answers. Adoption asks "is *every instance* on the new
release", which it cannot.

Each record carries:

| Field | Why |
|---|---|
| `process_class`, `instance_id` | Identity |
| `release_id`, `head_revision` | What it adopted |
| `adopted_at` | When it swapped |
| `reported_at` | When it last said so |
| `source` | Where it got the release, in the same vocabulary `PinnedConfigurationSnapshot.source` uses — so a process running the version-controlled baseline is not silently counted as having adopted a graph release |

`adopted_at` and `reported_at` are distinct on purpose: the gap between them is
how long the process has been quietly serving the release, and a process that
adopted an hour ago and reported a second ago is healthy, not stale.

## Configuration domains

| Domain | Owns |
|---|---|
| `RETURN_PLATFORM` | Agents, discovery, clarification, workflow, return policy, integrations, feature flags, source-resolution behaviour, business calendars |
| `AI_GATEWAY` | Task system prompts, prompt versions, provider allowlists, token limits, retry, rate limiting, circuit breakers, deterministic fallback selection |
| `DEPENDENCY_SIMULATION` | Simulation contracts, operation sequences, narrative behaviour, provider order, timeouts, pricing assumptions |

Production and staging **fail closed** when `AI_GATEWAY` or
`DEPENDENCY_SIMULATION` is absent.

Per-family security classification, editability, hot-change support and rollback
implications are in [`../configuration/families.md`](../configuration/families.md).

## Secrets

**The process environment is the runtime source for credential values.** Neo4j
stores no credential value and no pointer to one: a `credential` block on a data
source carries a `profile_key`, which is an identity AI route bindings address, and
nothing else.

Runtime processes read credentials once, when creating or refreshing clients — not
per business query.

**Vault is optional and disabled by default.** Set `PLATFORM_VAULT_ENABLED=true`
and give each credential a `*_SECRET_REFERENCE` holding a
`vault://secret/production/<path>#<key>` URI, and those references are resolved
into memory during the startup sequence above, before any client is created.

**Secrets must never be stored in** Neo4j, MongoDB documents, Valkey, Temporal
payloads, frontend storage, logs, evidence files or AI traces.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/config/runtime` | The active runtime configuration this process is serving |
| `GET` | `/api/config/adoption` | `LIVE` / `ACTIVATING` / `NO_ACTIVE_RELEASE`, per class |
| `GET` | `/api/config/releases` | Release history |
| `GET` | `/api/config/releases/{release_id}` | One release |
| `POST` | `/api/config/releases/{release_id}/promote` | Activate. Requires the expected head revision. |
| `GET` | `/api/config/audit`, `/api/config/audit/{audit_id}` | Who changed what |
| `GET` | `/api/config/sources`, `/api/config/sources/{id}`, `/api/config/sources/{id}/assets/{asset_id}` | Configured sources |
| `GET` | `/api/runtime-config` | The shell's boot read |

## Related

- [`../configuration/families.md`](../configuration/families.md)
- [`../screens/configuration.md`](../screens/configuration.md)
- [`../operations/startup.md`](../operations/startup.md)
- [`identification-fields.md`](identification-fields.md)

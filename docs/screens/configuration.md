# Configuration

**Route** `/config` · **Capability** `config.runtime.read` ·
**Components** `frontend/src/domains/config/ConfigurationPage.tsx`,
`AgentsSection.tsx`, `JsonView.tsx`

## Purpose

Change how the platform behaves, and release the change safely.

## Sections

`/config/{slug}`, from `CONFIG_SECTIONS`:

| Section | Shows |
|---|---|
| Overview | The active release, head revision, checksum, source |
| Agents | Per-agent configuration — `AgentsSection` |
| Runtime | The resolved runtime configuration this process is serving |
| Releases | Release history, diff, promote |
| Integrations | Integration topics and their authorities |
| Business | Return policy, business calendars, timings |
| Modules | Registered module descriptors and their state |
| Security | Capability and role configuration |
| Audit | Who changed what, and when |

**"Data Sources" is deliberately absent** from this list and is its own domain.
See [`data-sources.md`](data-sources.md).

## Key/value ↔ raw JSON consistency

Each editable section offers both a structured view and a raw JSON view
(`JsonView`). They are **two renderings of one document**, not two documents:
editing either produces the same draft, and switching views mid-edit does not lose
or duplicate a change.

The raw view exists because the structured view cannot cover every shape a
configuration family may take, and a family the structured editor does not know
about must still be editable rather than invisible.

The merged document must pass **complete typed validation** before it is written
to the draft — `ReturnPlatformConfiguration` for `RETURN_PLATFORM`, and the
equivalent for `AI_GATEWAY` and `DEPENDENCY_SIMULATION`. A draft that would not
validate is refused at edit time, not at publish time.

## Versions, diff, rollback, activation

```text
DRAFT → VALIDATED → RELEASED → SUPERSEDED → ARCHIVED
```

- **Clone** the active release to start a draft.
- **Validate** — full typed validation of every domain in the release.
- **Diff** — a release against its predecessor.
- **Publish** requires the **expected head revision**. Two administrators editing
  from the same starting point cannot both activate; the second gets a revision
  conflict.
- **Rollback** is a forward operation: promote an earlier release. Published
  releases are immutable and are never edited back.

Published releases are checksum-verified. A checksum mismatch **refuses
activation**.

## Adopted-release readback

The Overview section shows what **this API process** is serving. That is not the
same question as "is the release live", and the screen keeps them apart:

| Read | Question |
|---|---|
| `GET /api/config/runtime` | What is *this process* serving? |
| `GET /api/config/adoption` | Has *every required process class* adopted the activated release? |

`ACTIVATED != LIVE`. The adoption read reports `LIVE` only when every required
class has at least one live instance and **all** of them report the activated
release id **and** head revision.

The per-class breakdown — and the gap when activating — is on the
[Operations](case-operations.md) screen, where the operator asking "why is this
case behaving like the old configuration" actually is.

## Actions

| Action | API | Side effects | Reversible |
|---|---|---|---|
| Read the active release | `GET /api/config/runtime` | none | Yes |
| Browse releases | `GET /api/config/releases` | none | Yes |
| Open a release | `GET /api/config/releases/{release_id}` | none | Yes |
| **Promote a release** | `POST /api/config/releases/{release_id}/promote` | **Activates it.** Every process reconciles; new work uses the new release. | Forward-only: promote a different release |
| Read audit | `GET /api/config/audit`, `/{audit_id}` | none | Yes |

Promotion is the irreversible one. It requires write authorization on top of the
read capability that makes the domain visible.

## Backend APIs consumed

| Method | Path |
|---|---|
| `GET` | `/api/config/runtime` |
| `GET` | `/api/config/adoption` |
| `GET` | `/api/config/releases` |
| `GET` | `/api/config/releases/{release_id}` |
| `POST` | `/api/config/releases/{release_id}/promote` |
| `GET` | `/api/config/audit`, `/api/config/audit/{audit_id}` |
| `GET` | `/api/agents`, `/api/agents/{manifest_id}` |
| `PUT` | `/api/agents/{manifest_id}` |
| `GET` | `/api/principal` |

## Live-state behaviour

Polled on interval and on focus.

Other API processes detect a new graph-head revision and activate the same
validated domains **without a restart**. The AI route pool is rebuilt at the same
activation boundary. So a promotion made in one process becomes visible here
within a poll cycle, and the screen is reading a state that genuinely changes
under it.

## Loading, error and empty states

| State | Renders | Distinguished from broken by |
|---|---|---|
| No active release | `NO_ACTIVE_RELEASE`, with the bootstrap command to publish one | Explicit and actionable |
| Draft fails validation | The validator's own errors, per field path | Not a generic "invalid" |
| Revision conflict on promote | An explicit conflict message naming the current head revision | The operator can reload and retry rather than guessing |
| Checksum mismatch | Refusal, stated as a checksum failure | This is a security control, not a transient error |
| Load failure | Error panel with correlation id | |

## Persistence and data source

**Neo4j is the authoritative control-plane store.** Runtime processes compare the
graph head revision with their last-good immutable snapshot; they do **not**
traverse the configuration graph per request.

MongoDB retains the digest-addressed runtime snapshot as **audit evidence**. It is
not an editable configuration authority, and this screen never writes to it.

Packaged YAML under `backend/config/` is bootstrap/default input only and is
**never rewritten at runtime**.

Secrets stay in the process environment. Graph configuration stores only validated credential identities
and receipts. The frontend never receives a secret value.

## Audit effects

Every administrative action here is audited: the principal, the timestamp, the
before/after and the resulting checksum. Readable in the Audit section and at
`/api/config/audit`.

Graph migrations are checksum-tracked in `ConfigurationMigration` nodes. A modified
migration file is rejected after application.

## Configuration dependencies

This screen *is* the configuration surface, so its dependencies are structural:

| Dependency | Effect |
|---|---|
| `RETURN_PLATFORM` domain | Agents, discovery, workflow, policy, integrations, calendars |
| `AI_GATEWAY` domain | Prompts, providers, limits, retries, breakers |
| `DEPENDENCY_SIMULATION` domain | Simulator contracts and behaviour |
| Deployment wiring, DB schema, graph migrations | **Not editable here** — they are infrastructure contracts, version-controlled, not agent behaviour |

Production and staging **fail closed** when `AI_GATEWAY` or
`DEPENDENCY_SIMULATION` is absent from the active release.

Per-family classification and hot-change behaviour:
[`../configuration/families.md`](../configuration/families.md).

## Known constraints

- Promotion is forward-only; there is no in-place rollback.
- The structured editor does not cover every family; those use raw JSON.
- Infrastructure endpoint changes are restart-required and cannot be hot-applied
  from here.
- Per-class adoption detail lives on the Operations screen, not here.

# Configuration

**Route** `/config` · **Capability** `config.runtime.read` ·
**Components** `frontend/src/domains/config/ConfigurationPage.tsx` (the shared
chrome), `TypedSectionScreen.tsx` (the typed-form/Advanced-JSON toggle every
section below rides), one `*Section.tsx` per row of the table, the shared
primitives in `frontend/src/components/forms/*`, and `DocumentEditor.tsx` (the
Advanced/JSON mode, kept as the escape hatch for a shape the typed form does
not cover — never the default any more).

## Purpose

Change how the platform behaves, and release the change safely.

## Sections

`/config/{slug}`, from `CONFIG_SECTIONS` (`frontend/src/domains/registry.ts`) —
this is the CFG-4/5/6/8 typed-screen set, current as of CFG-7; there is no
longer a single generic "Business" tab (retired by CFG-5 once every section it
carried had a typed screen of its own):

| Section | Slug | Shows |
|---|---|---|
| Overview | `overview` | The active release, head revision, checksum, source, adoption status, and the **undecided-keys panel** (below) |
| Agents | `agents` | The live `agents:` block as a typed table (enabled, AI-assisted, route ref); the proposal path is shown inline |
| Support | `support` | Six tabs over six keys: Template (with preview), Gate, Ingress, Resolver, Context assembly, Queues |
| Discovery | `discovery` | Identification fields, aliases, source paths, clarification policy, selection vocabulary |
| Return Policy | `return-policy` | Return method derivation, ship-via map, requirements matrix |
| Policy | `policy` | Eligibility: the policy-evaluation switch, defaults for all products, exceptions, precedence, and a decision preview (`POST /api/config/policy/preview` against the **draft**, never a real case) |
| Fulfilment | `fulfilment` | Shipment status ladder, bay rules, order-management rules |
| Deployment | `deployment` | The env→release business switches (below) |
| Workflow | `workflow` | Stage sequence and SLAs, waits/timeouts, business calendars, housekeeping |
| Runtime | `runtime` | The resolved runtime configuration this process is serving |
| Releases | `releases` | Release history, diff, promote |
| Integrations | `integrations` | Integration topics and their authorities, copilot settings, fabrication-guard switches |
| Simulation | `simulation` | `DEPENDENCY_SIMULATION`: enabled, banner, AI narration, dependencies table |
| Source Bindings | `source-bindings` | Source-binding overrides and the sync trigger/run history (moved here from `/sync` by CFG-5; `/sync` still redirects) |
| Modules | `modules` | Registered module descriptors and their state |
| Security | `security` | Capability and role configuration |
| Audit | `audit` | Who changed what, and when |

**"Data Sources" is deliberately absent** from this list and is its own domain
(the Graph Schema Analyzer's connections, not this domain's Source Bindings).
See [`data-sources.md`](data-sources.md).

## The `deployment` section (D-CFG-4, CFG-6)

Eight business switches that used to live only in `.env`/compose now live in
the release, at `deployment`, hot-adopted like everything else in this table:
AI provider order, per-provider model pools, the two GOOGLE extras, the four
dependency-simulation modes, feedback learning, and the support-ticket
mode/base URL. Every option production refuses to run (`SIMULATOR`/`MANUAL` in
provider order, a `SIMULATED` dependency mode) renders **disabled with the
reason on the option**, not hidden — and the same refusal is enforced on
**every** `RETURN_PLATFORM` publish or adopt-packaged call, not only one that
touches `deployment` itself, plus again at process startup. Full detail,
including the env-as-bootstrap-default rule and the `runtime_integrations`
precedence: [`../configuration/families.md`](../configuration/families.md).

## Undecided keys and `--adopt-packaged-key`

The bootstrap decides, per top-level `RETURN_PLATFORM` key and per unit of the
other two domains, whether a value in the active release was an operator's
edit or merely predates a change to the packaged file. A key it cannot decide
is **undecided**: named in a `packaged_configuration_not_adopted` warning, and
listed on the Overview section with a per-key **"Take packaged file"** action
that calls `POST /api/config/adopt-packaged` for that one unit — the UI path
onto the same per-unit adoption the bootstrap's own `--adopt-packaged-key` CLI
flag performs. Detail: `return_platform/configuration/README.md`
("Carry-forward").

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
| `POST` | `/api/config/validate/{domain_key}` — path-mapped validation errors, used by every typed screen's Validate action (CFG-3a) |
| `POST` | `/api/config/publish` — open-from-active, patch, VALIDATED, RELEASED in one call, `expected_head_revision`-locked (CFG-3a); the pipeline every typed screen's Publish action drives |
| `POST` | `/api/config/adopt-packaged` — per-unit undecided-key adoption (Overview's "Take packaged file", and the bootstrap's `--adopt-packaged-key`) |
| `GET` | `/api/config/packaged/{domain_key}` — the packaged baseline, for "Reset to packaged default" links |
| `GET` | `/api/config/packaged-drift` |
| `POST` | `/api/config/policy/preview` — evaluates a sample against the **draft**, never a real case (Policy section) |

Every other typed section (Discovery, Return Policy, Fulfilment, Deployment,
Workflow, Support, Integrations, Simulation, Source Bindings) rides the same
three routes above (`validate`, `publish`, its own `runtime` slice) rather than
a section-specific endpoint; see each section's own `*Section.tsx` for the
`domainKey`/patch paths it edits, or the committed OpenAPI document for the
full route list.

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
| `RETURN_PLATFORM` domain | Agents, discovery, workflow, policy, integrations, calendars, and (CFG-6) `deployment` — the business switches, editable at `/config/deployment` |
| `AI_GATEWAY` domain | Prompts, providers, limits, retries, breakers |
| `DEPENDENCY_SIMULATION` domain | Simulator contracts and behaviour |
| Infrastructure deployment wiring (hosts, ports, compose/k8s), DB schema, graph migrations | **Not editable here** — version-controlled infrastructure contracts, not business behaviour. Do not confuse with the release's own `deployment` section above, which CFG-6 moved out of `.env` precisely so it *would* be editable here. |

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

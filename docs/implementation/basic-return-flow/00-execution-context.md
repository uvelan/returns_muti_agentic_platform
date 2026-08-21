# 00 · Execution context

Authoritative prompt: `BASIC_RETURN_FLOW_CONTINUOUS_STABILIZATION_PROMPT.md`
(user-supplied). This file is the resume point. Read it first after any
interruption.

## Baseline

| Item | Value |
|---|---|
| Branch | `refactor/unified-return-platform` |
| Baseline commit | `47f5abd7fad4e9f0e2c890ef7e762b37e45296e6` |
| Working tree at start | clean (`git status --short` empty) |
| Session start | 2026-08-21 |

The session began on `feat/teams-bots-windows-first` @ `7340ce2`. That branch is
docs-only on top of `47f5abd`; the Teams work is parked by the directive, so
execution moved to the development line it branched from. No new branch created.

## Objective

Stabilise the in-application basic return flow end to end:

```
order number -> exactly one order -> confirm order and line -> capture configured
return details -> Workflow Agent invokes Bay Assignment Agent -> persist bay ->
one support work item carrying return and bay detail -> complete template
rendered in Support Chat UI -> stop before RMA
```

## Excluded scope

Teams bots, Teams gateway, manifests, dev tunnel, Azure bot credentials, Teams
proactive notifications, RMA creation, shipping-label generation, tracking
updates. Existing parked code and docs are preserved untouched.

Policy evaluation is disabled through runtime configuration only. Never deleted,
never branched around in code.

## Services and dependencies

### Docker, infrastructure only (`compose.yaml`, default profile)

`mongodb`, `mongodb-rs-init`, `sqlserver`, `sqlserver-init`, `neo4j`, `valkey`,
`temporal`, `temporal-postgresql`, `runtime-configuration-init`.

Application services sit behind the `containerized-app` profile and are not
started; `temporal-ui` and `seed-runner` sit behind `dev-tools`.

### Host processes (`scripts/run_all_host.ps1`)

Backend (uvicorn), frontend (vite), and five workers: `temporal`,
`orchestrator`, `outbox`, `jobs`, `integration-outbox`.

## Selected seeded order

**`CQ800002`** -- chosen because it is unambiguous in every dimension the flow
touches.

| Field | Value | Source |
|---|---|---|
| Order number | `CQ800002` | `salesInv._id = "GARDEN*CQ800002"`, `salesHdrEventData.orderId` |
| Account | `GARDEN` | `salesHdrEventData.accountId` |
| Order status | `INVOICED` | `salesHdrEventData.orderStatus` |
| Lines | exactly 1 | `salesHdrEventData.numOfLines = "1"`, `salesLines` length 1 |
| Line number | `1` | `salesLines[0].salesLnsEventData.lineNumber` |
| Customer | `THELMA OSBORNE` | `salesHdr.salesHdrData.custName` |
| Product | `6X12 CEIL ALUM 4-WAY REG SAND` | `salesLines[0].lineData.productDesc` |
| Master product | `2175168`-style id | `salesLines[0].lineData.masterProductId` |
| Colour | `Sandtone` | `lkpSearchProduct.eco.colorFinish[0]`, joined on `masterProductId` |
| Shipped qty | 1 of 1 ordered | `lineData.shipQty` / `orderQty` |
| Warehouse | `686` "Louisville Distribution Center" | `salesHdrEventData.sellWhseId` |

Uniqueness is a property of the corpus, not of this order: aggregating
`salesInv` by `salesHdrEventData.orderId` returns **zero** ids appearing on more
than one document, across all 10,000.

Colour is genuinely present at the authoritative source
(`lkpSearchProduct.eco.colorFinish`, populated for 253 of 1000 products), so a
missing colour in the UI would be a mapping defect to fix -- not an absent value
to report as unavailable.

## Configuration locations

| Concern | Location |
|---|---|
| Return platform config | `backend/config/returns/production.yaml` |
| AI gateway tasks and providers | `backend/config/ai_gateway.yaml` |
| Policy modules | `backend/config/policies/*.yaml` |
| Agent definitions | `backend/config/agents/` |
| Typed settings | `backend/src/return_platform/configuration/settings.py` |
| Typed return config | `backend/src/return_platform/configuration/return_configuration.py` |
| Environment | `.env` (gitignored), `.env.example` |

## Active implementation state

Phases 1-3 complete. Phase 6's configuration switch is implemented (not yet
activated for the run). Phase 4 is in progress.

Runtime mode for this run:

| Setting | Value | Where |
|---|---|---|
| `PLATFORM_AI_PROVIDER_ORDER` | `MANUAL` | `.env` (backup at `.env.backup-basic-flow`) |
| `PLATFORM_AI_MANUAL_HANDOFF` | `UI` | `.env` |
| `interceptMode` | `false` | `PUT /api/v1/ai-gateway/settings` |

Host stack runs detached with per-process logs at `<scratchpad>/logs/*.log`,
started by `<scratchpad>/start_hosts.ps1` and restarted by `restart_hosts.ps1`
(both non-blocking mirrors of `scripts/run_all_host.ps1`). The flow is driven
through the real API by `<scratchpad>/flow.py`, which stands where the browser
stands.

## Last completed validation

V-10 -- one order number returned exactly one order through the normal agent
path, in manual mode, with no live provider reachable.

## Next executable action

Confirm order `CQ800002` and its line, then read the case projection to see
which primary details (line number, customer name, product name, colour) reach
the UI and which need a mapping fix.

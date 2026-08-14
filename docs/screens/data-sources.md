# Data Sources

**Route** `/data-sources` · **Capability** `config.source.read` ·
**Components** `frontend/src/domains/data-sources/DataSourcesPage.tsx`,
`SourceBindingsPanel.tsx`

## Purpose

Check the platform can still reach its data, and point a dataset somewhere else.

## Why it is its own domain (UI-02)

It was a tab inside Configuration that rendered `/api/config/sources` as raw
JSON. That made the platform's whole source surface a nested selection under a
domain most of its readers were not visiting: **"can we still reach the warehouse
database" is asked by people who are not editing configuration**, and the answer
was three clicks inside a screen about releases.

Sources are not a configuration *field*. They are what the platform reads from.

`registry.ts` records the absence of a "Data Sources" tab in `CONFIG_SECTIONS`
explicitly, so nobody adds it back.

## UI regions

**Source list** — every configured source: id, connector type, access mode,
health.

**Source detail** — for the selected source:

- connection metadata (**non-secret only** — the frontend never receives a secret
  value);
- the Vault reference and its version, as a reference, never a value;
- the validation receipt: what was verified and when;
- **what the source exposes** — the collections, tables and indexes it declares;
- health and last check.

**Dataset bindings** (`SourceBindingsPanel`) — where each dataset is bound.
This is the "point a dataset somewhere else" half.

**Contextual rail** — source facts and notes.

## Access mode, and the read-only guarantee

Every source shows its access mode. For source MongoDB and source SQL Server this
is **read-only, and it is enforced in code, not configuration**:

> Graph configuration **may narrow** access. It **cannot broaden** it.

An operator cannot configure a source into writability from this screen or any
other. The requested access mode is validated against the **code-owned connector
capability** during activation, and a request for more than the connector can do
is refused.

The platform's own return tables in the same SQL Server instance are a different
thing — platform-owned and read/write. See
[`../architecture/security-boundaries.md`](../architecture/security-boundaries.md)
for the distinction, which the README previously collapsed.

## Actions

| Action | API | Side effects | Reversible |
|---|---|---|---|
| Select a source | `GET /api/config/sources/{source_id}` | none | Yes |
| Inspect an asset | `GET /api/config/sources/{source_id}/assets/{asset_id}` | none | Yes |
| Bind a dataset | `PUT /api/source-bindings/{dataset}` | Changes which source a dataset reads from | Yes — rebind, or `DELETE` |
| Clear a binding | `DELETE /api/source-bindings/{dataset}` | The dataset reverts to its default resolution | Yes — rebind |

**What this screen offers is what the backend actually supports, and no more.**
There is no "add a source", no "edit credentials" and no "test connection here",
because source creation and credential validation run through the runtime
credential-validation control plane — the only place a raw credential is ever
accepted — and duplicating an entry point for them here would be a second,
weaker path to the same secret.

## Backend APIs consumed

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/config/sources` | Source list with health |
| `GET` | `/api/config/sources/{source_id}` | One source: metadata, receipt, exposed datasets |
| `GET` | `/api/config/sources/{source_id}/assets/{asset_id}` | One asset's shape |
| `GET` | `/api/source-bindings` | Current dataset bindings |
| `PUT` | `/api/source-bindings/{dataset}` | Bind a dataset to a source/asset |
| `DELETE` | `/api/source-bindings/{dataset}` | Clear a binding |

## Live-state behaviour

Health is polled. It is a **cached backend health result**, not a live probe fired
by opening the screen — opening a source page must not open a database connection,
or a screen refresh becomes a load test against production.

The screen shows when each health result was taken.

## Loading, error and empty states

| State | Renders | Distinguished from broken by |
|---|---|---|
| No sources configured | "No data sources are configured" | Explicit wording |
| Source unreachable | The health result's own failure reason, on the source row | This is **data**, not a screen error — the screen loaded fine and the source did not |
| Source list load failed | Error panel with correlation id | No source rows at all, versus rows with failing health |
| Asset list empty | "This source declares no datasets" | Distinct from "could not read the source" |
| No bindings | "No datasets are bound" | |

The important distinction on this screen is between **"the platform cannot reach
the source"** (a health result, rendered per-source) and **"the platform cannot
tell you about its sources"** (a screen error). Collapsing them would make an API
outage look like a total data-layer outage.

## Persistence and data source

Source configuration lives in the **Neo4j configuration control plane**. Vault
holds credential values; Neo4j holds only versioned references. Health results and
validation receipts are stored with the configuration.

Dataset bindings are configuration, versioned with the release.

## Audit effects

Binding and clearing a dataset are administrative actions and are audited —
readable at `/api/config/audit`.

Source activation records a receipt bound to connector type, endpoint,
configuration checksum and the exact Vault secret version.

## Configuration dependencies

| Family | Effect | Restart |
|---|---|---|
| `sources` | The whole screen | Binding changes are hot; **endpoint changes are restart-required and fail closed** |
| `PLATFORM_DATA_SOURCE_ALLOWED_HOSTS` (env) | Which endpoints may be configured at all | Restart |
| `data_assets` | Which datasets exist to bind | Hot |

Infrastructure endpoint changes deliberately require a restart. A running process
holds live client pools against the old endpoint, and swapping the endpoint under
them would leave half the process talking to each.

## Known constraints

- No source creation or credential entry here, by design.
- Health is cached, not probed on demand.
- No per-asset row-count or freshness metric.

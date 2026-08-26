# TC-E2E-03 — Diff summary from pre-work SHA

Pre-work rollback point: `138bbb80b3e74bd59526bc208fe4c4d5e7b8f745`.

## Backend

- `configuration/return_configuration.py` — `ShipmentStatusConfiguration` (code, label, ladder, ordinal, terminal, exception_state, color_token, allowed_next) and `ShipmentTrackingConfiguration` (statuses, initials per ladder, freight_methods, collection, field map) with validators: unique codes per ladder, allowed_next stays on its ladder, a terminal status allows nothing, initials sit on the right ladder.
- `config/returns/production.yaml` — the catalog: 19 statuses across the parcel and freight ladders exactly as specified, `shipmentInfo` collection, empty field map (canonical names).
- `operations/shipment_tracking.py` (new) — the store: idempotent seed on (return record id, tracking-or-BOL), parcel refuses to seed without tracking, freight seeds on its BOL with `pro_number` honestly absent until the carrier assigns it; catalog-validated event append with audited override; all reads reverse-mapped to logical names.
- `operations/fulfillment_progress.py` (new) — catalog-classified consumption: terminal closes the record (fulfilledAt) and the case once every record is terminal; exception states surface as case facts and clear on resume; nothing invented.
- `api/shipment_console.py` (new) — `GET /shipment-status-catalog`, `GET /shipments`, `GET /shipments/{identifier}`, `POST /shipments/{id}/events` (capability-gated, 422 with allowedNext on refused transitions, drives the existing C4 chain keyed on PRO → tracking → BOL).
- `workflows/return_case_activities.py` — `_seed_return_shipments`: one document per return record from the parsed support reply, mode from the release's freight methods, RMA from the incoming notice, skip-with-log when a parcel has no tracking (never invent).
- `operations/return_shipment_state.py` — `_append_once`: repeated identical fulfillment readings are the documented no-op.
- `tests/test_shipment_tracking.py` (new) — 9 tests: seed idempotency, ladder selection, refusal without identity, transition validation, terminal + override, field renames, lookup by every identifier, catalog validators.

## Frontend

- `api/shipments.ts` (new) — console API client; documents are open records read through logical-name accessors; no status literal anywhere.
- `domains/shipments/ShipmentConsolePage.tsx` (new) — the console: filterable list, detail with stage rail (exception fork row), append-only event log, update panel whose dropdown is the catalog's allowed_next, audited allow-any override, terminal banner with reopen-by-override.
- `domains/registry.ts` + `domainScreens.ts` — the Shipments domain (Truck, `/shipments`).
- Regenerated OpenAPI types; mock runtime-config handler kept in contract.

## Harness (qa/tc-e2e-02)

- `run_flow.py` — TC-E2E-03 stages 17-24: seed assertions, catalog-driven ladder walk (harness-as-carrier assigns the PRO at the origin-terminal rung), fulfillment/close/notify assertions, exception drill (forward resume), idempotent-replay check; `--tc03/--freight/--multi/--exception-drill`; freight keywords read from the live release.
- G6 patch/revert payloads (`patch_g6_rename.json`, `patch_g6_revert.json`).

## Evidence

- `evidence/TC-E2E-03/` — LEDGER.md (18 runs, gate summary), DEFECTS.md (5 platform defects, 3 harness faults, 1 infra storm), SCHEMA_REPORT.md (zero generations), per-run JSON archives, raw-doc rename proof (run 17), console screenshots (`ui/`).

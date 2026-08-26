# TC-E2E-03 — Defect log

Platform defects found by the loop, in the order the runs surfaced them. Harness-only
faults and infra events are ledgered but not defects; they are listed at the bottom
for completeness.

## Platform defects (fixed, committed)

| # | Found by | Step | Defect | Fix (commit theme) |
|---|---|---|---|---|
| D1 | run 2 | 16 | The seeded shipment document carried an empty `rma_reference`: the RMA lives on the incoming notice (`plan.incoming.return_reference`), not in the merged support fields, so console lookup by RMA 404'd. | fix(shipments): the seeded shipment carries its RMA |
| D2 | run 4 | 18-19 | A repeated fulfillment reading 500'd: `_record_on_case` insert-only append collided with the derived fact id on the second identical AWAITING_HANDOFF reading, despite the documented idempotence. | fix(shipments): a repeated fulfillment reading is the documented no-op |
| D3 | run 5 | 20 | When the workflow had already closed the case at business-complete, FulfillmentProgress skipped the `ALL_RETURNS_DELIVERED` case fact, so the all-delivered signal never landed on such cases. | fix(shipments): the all-delivered fact lands even on a business-complete case |
| D4 | run 9 | 16 | Freight could never be seeded: Support's requirement table issues no tracking number for LTL (the PRO arrives from the carrier at the origin terminal), and the store demanded a tracking reference to seed. | fix(shipments): freight seeds on its BOL; the PRO arrives with the carrier |
| D5 | post-G7 UI pass | console | The Shipments domain was never registered in `frontend/src/domains/registry.ts` — the page, route and API client existed, but no operator could reach them from the launcher or by URL. | fix(console): the Shipments domain joins the registry |

## Design decision recorded (not a defect)

The return workflow closes a case at business-complete (RMA + label + tracking all
present); `REQUIREMENT_DIMENSIONS` deliberately has no delivery dimension. The case
projection maps that CLOSED to `COMPLETED_EXTERNAL_SETTLEMENT` when no settlement
producer ran. FulfillmentProgress therefore stamps the case-fulfilled fact and closes
records regardless of whether the workflow already closed the case; the harness
accepts the closed family. Rewriting the workflow lifecycle to add a delivery
dimension was judged out of scope for this loop.

## Harness-only faults (no platform change)

- run 3 — evidence-dir collision (added `TCE2E02_RUN_BASE`).
- run 8 — the harness matched freight keywords against its own stale list instead of the release's (`_freight_keywords()` now reads `/api/config/runtime`).
- run 14 — the drill resumed backwards to the fork rung; the catalog rightly refused (422). The drill now resumes forward to one of the exception's `allowed_next` rungs.

## Infra events (neutral)

- runs 10-12 — SQL Server container crashed (dump 01:50Z) taking neo4j with it; the backend booted mid-crash, the return-workflow worker died, and the order-discovery worker kept a dead bolt pool. Containers restarted to healthy, workers restarted; no platform change.

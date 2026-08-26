# TC-E2E-03 — Schema report

**Candidate schema generations proposed: zero.**

The shipment-tracking loop needed no change to the running graph schema. Everything
new lives in release configuration and in one Mongo collection the release names:

- The status catalog (both ladders, transitions, labels, colours) is the
  `shipment_tracking` block of the release — codes appear nowhere in Python or JSX.
- Return-shipment documents live in the configured collection (`shipmentInfo`),
  marked `kind: returnShipment`, under field names the release maps (G6 proved a
  physical rename lands without touching a client).
- The authoritative status chain reuses contract C4 exactly as built:
  `dbo.return_tracking` (SQL) → graph sync → case facts → associate conversation.
  No new SQL column, no new graph entity or relationship, no new check constraint.

The one place a new dimension was *considered* — a delivery requirement on
`REQUIREMENT_DIMENSIONS` — was recorded as a design decision, not a schema change
(see DEFECTS.md): the workflow closes at business-complete and fulfillment closes
records and cases from the catalog's terminal classification.

# TC-E2E-02 Schema Justification Report

One row per candidate graph-schema generation created during the cycle.

| gen | diff class | what changed | triggering step/failure | why config alone couldn't fix it | migration path | validated |
|-----|-----------|--------------|------------------------|----------------------------------|----------------|-----------|

**No candidate generations were created.** Every failure the cycle surfaced was resolved
by code fixes in the platform (see DEFECTS.md D1–D5) or by ordinary configuration
releases (clarification fields, return-details gate, method derivation, bay pre-arrival,
provider ranking, prompt versions). The active schema generation
(`return-order`, generation `5a394ff8…` at campaign start) answered every graph read the
flow needed — fuzzy customer resolution via the `customer_name_search` fulltext index,
`customer → PLACED_ORDER → sales_order` traversal, order lines, return records, and the
return-table sync — without a single entity or field the approved schema lacked.

Per the mission's own test: a schema edit would have been a mapping/config bug papered
over with a generation. None was needed, so none was made, and the active generation was
never touched.

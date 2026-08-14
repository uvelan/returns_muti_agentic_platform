# Bay Assignment

**Current as of 2026-08-14, commit `dcbb7dc`.**

Bay Assignment recommends where a returning item should physically go. It is a
**best-effort decision service** started after order confirmation and run
concurrently with the support conversation.

## The contract (C2)

The result is **one atomic recommendation**: warehouse, bay, return location,
**computed** confidence, reason, explanation, and the evidence reference — all
populated from the same reading.

**A partial result is not a result.** A caller that had to join a bay id to a
confidence from somewhere else is the exact shape this was built to close.

`confidence_millionths` is `BayAssignmentAgent`'s computed margin of the winning
bay over the runner-up. It is **never a constant**. It is `None` only when no
recommendation was produced at all — which is a different thing from a
recommendation made with low confidence, and the two must not be collapsed.

## What was wrong

`WarehousePlacementService` was a real, graph-oriented, deterministic bay engine
that the case flow never called. Its only references were
`api/warehouse_placement.py`. Meanwhile `request_bay_assignment` — the one
activity the case workflow ran for a bay — wrote a single
`bay_assignment_requested` fact and returned. It queried no graph, ranked no bay,
and resolved no location. The workflow then waited `bay_wait_seconds` for a
`bay_result` signal whose only sender in the whole repository was a test.

So a bay was requested, nothing computed one, and the workflow waited out its
timeout every single time.

`CaseBayPlacement` is a **re-keying of the existing engine, not a second one**.
The observation port, the candidate pipeline (`observe_eligible_bays`) and the
ranking agent are the ones the session path already used.

## Where the inputs come from

That re-keying is the whole difference between a session and a case:

- a **session** carries `processingWarehouseReference`, `productType`,
  `approvedReturnMethod`, and a handling unit with a physical status;
- a **case**, at the moment Bay runs, carries a confirmed order and its fact
  log, and **no handling unit exists yet** because nothing has been received.

Inputs are read from the case's fact projection, falling back to the confirmed
order's own shipping warehouse in the graph. Neither is guessed. An unresolvable
warehouse yields `ABSENT / NO_WAREHOUSE_REFERENCE` — a real answer the
observation model already distinguishes from "this warehouse has no eligible
bay".

## Candidates come from the graph

Candidates used to come from `SQLBusinessStateRepository.list_bay_candidates` on
every call. That is a direct source read from an agent path, which the source
read-only policy forbids, and it answered a return with no warehouse reference by
offering **every bay in the estate**.

The graph read is an *observation* with three outcomes, and a warehouse nobody
observed yields **no** candidates rather than all of them.

A graph that cannot be reached does not fall back to the SQL bypass. Silently
reading the source the step removed would make the removal a comment.

## Capacity evidence

`capacity_evidence` records which capacity figure the ranking actually weighed.
`DECLARED` means the live reservation aggregate could not be read — so the chosen
bay may already be full and the reservation may refuse it. An operator reading a
recommendation needs to know which of those two they are looking at.

## Best-effort, concretely

Nothing in placement raises for a missing warehouse, an unreadable graph, or an
ineligible estate. Each is a recommendation carrying a reason. The only exception
that escapes is one the caller's activity turns into `REQUEST_FAILED`.

Assignment status is one of:

| Status | Meaning |
|---|---|
| `ASSIGNED` | A bay was found. This no longer requires `IN_TRANSIT` fulfilment — assignment starts on confirmation and runs alongside the rest of the return, so the shipment usually does not exist yet. |
| `NOT_APPLICABLE` | Nothing is coming back physically. No bay will ever be needed. |
| `PENDING` | Sought and not found, or not sought yet. The return proceeds and the bay may be filled in later. |

`PENDING` and `NOT_APPLICABLE` are kept distinct because "not yet" and "never"
mean different things to an operator looking at a parked return.

## Timing

`bay_wait_seconds` (default 120, range 0–86,400) bounds how long the workflow
waits for a result.

It is deliberately **not** a business-calendar duration, unlike the support wait
and reminders. It bounds dead time on the critical path while an associate is
sitting there; stretching it across a weekend would leave a live conversation
hanging. Short on purpose — measure before raising it.

## What downstream may assume

Nothing. **Nothing downstream may depend on a bay being present.** Failure,
timeout or low confidence never blocks the return; the case proceeds without
placement and records why.

## Related

- [`canonical-runtime-flow.md`](canonical-runtime-flow.md) §4
- [`security-boundaries.md`](security-boundaries.md) — the source read-only rule this respects
- [`../screens/case-operations.md`](../screens/case-operations.md) — where an operator sees the recommendation

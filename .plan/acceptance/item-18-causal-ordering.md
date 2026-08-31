# Acceptance item 18 — the ordering that exists, audited; the half that does not, made checkable

Item 18: *downtime backlog drained in order per case; **causal ordering (outbound
waits for its inbound's classification, unrelated approval does not)***. Dispatch
condition 3: **assert the chain, not just the drain.**

**Test:** `backend/tests/acceptance/test_item_18_causal_ordering_and_the_half_that_is_unreachable.py`
— 3 tests, normal suite, green.

**The gate that runs it** (RV rule 13, applied to ACC's own guard): no
`_real_infra` suffix, no `live_infra` marker, so it is in the default backend
suite that `.github/workflows/checks.yml` runs on every push. Stated because the
same audit shows **CI runs no live-infra test at all** — `addopts` deselects
`live_infra` and `browser`, and the workflow's backend job is plain
`pytest tests`. Any `_real_infra` scenario is a guard whose only gate is a human
running `scripts/dev/run_real_infra_suite.sh`.

---

## Part 1 — the first half is covered in-slice, and ACC audited it by injection

Not duplicated. `tests/operations/test_support_ingress_store.py` builds the chain
with the **real** `DurableSupportIngressStore` and drains it with the **real**
`IntegrationOutboxDispatcher`, with the queue deliberately loaded *against* the
answer (newest command made oldest-due, so a chain-blind worker would deliver
3, 2, 1). The chain itself is asserted separately, on `causationId` **and**
`requiredPredecessorIds`.

Reading that is not verifying it. Two injections:

| # | injected fault | result |
| --- | --- | --- |
| INJ-18a | `required_predecessor_ids=()` in `ingress_store._classify_command_fields` — the chain not populated | **5 failed, 9 passed** — the drain test, the causation test, both concurrent-arrival chain tests, and the parked-backlog reprocessing test |
| INJ-18b | `causation_id=None`, **predecessors kept** | **1 failed, 27 passed** — only `test_every_enqueued_event_carries_its_causation` |

**INJ-18b is dispatch condition 3, measured.** Dropping causation while leaving
the predecessors intact reds the *chain* assertion and leaves the *drain* green.
The two are therefore genuinely separable, the chain assertion is independently
load-bearing, and "assert the chain, not just the drain" is satisfied — not by
reading a test name, but because the drain demonstrably survives a chain defect
that the chain assertion catches.

Both reverted with `git checkout`; `git status` clean after each.

## Part 2 — the second half is not implemented, and is now checkably absent

Measured against `src/`:

* **Only two of the four streams §7 declares have a producer.** `inbound` (the
  ingress store) and `review_commands` (the case-command store). Nothing in
  `src/` names `CaseStream.OUTBOUND` or `CaseStream.OMC` — an AST walk over
  attribute accesses, not a grep, so prose in a docstring cannot manufacture a
  producer that does not exist.
* **Exactly two call sites reach `required_predecessor_ids` with a value**, and
  neither expresses a cross-stream dependency: the outbox **reader** rebuilding
  a command from a stored document, and the ingress store — the only producer,
  and it names the previous event on **its own** stream.
* **The machinery would take a cross-stream predecessor.**
  `ordered_command_fields` validates a predecessor's *existence on the case*,
  not its stream. Demonstrated behaviourally: an inbound event is enqueued and a
  `review_commands` event naming it is allocated successfully — the exact shape
  "outbound waits for its inbound's classification" needs.

So the mechanism exists and nothing uses it: **RV rule 13's shape, in the
ordering plane.** "Outbound waits for its inbound's classification" has no
outbound event to wait, and "unrelated approval does not" has nothing to be
unrelated to.

The three reads are asserted as **exact sets and counts**, not as absences — a
`not in` check cannot see a fourth stream appearing or a second population in a
file that already had one. Counted per file rather than pinned by line number: a
line pin fails on any edit above it, which trains people to update the number,
and a number people update on sight is not a guard.

| # | injected fault | result |
| --- | --- | --- |
| INJ-18c | `case_commands` made an **outbound** producer naming an inbound predecessor — the half becoming reachable | **2 failed, 1 passed** — the stream set and the population count; the machinery test correctly unaffected |
| INJ-18d | `ordered_command_fields` made same-stream-only | **1 failed, 2 passed** — only the machinery test, which would then require the opposite report: that cross-stream ordering is forbidden rather than merely unused |

### The scan looked at nothing, and the assertion's shape is what caught it

`_SOURCE_ROOT` was written as `parents[3]`, which points at the repository root,
not `backend/`. `Path.rglob` on a directory that does not exist **yields nothing
and raises nothing**, so both scans returned empty and reported "no streams
named, no call sites populating."

An assertion phrased the natural way — `assert "OUTBOUND" not in named` — would
have passed, and passed *for exactly the reason the finding claims*, which is
the worst available failure: the test would have agreed with the conclusion
while having looked at no source at all. The exact-set form failed on the first
run instead.

Fixed to `parents[2]`, and `_scanned_files()` now refuses a scan that finds
fewer than a hundred modules, so the same mistake cannot recur silently. Third
instrument defect ACC has found in its own work this run, and the third of the
same family: green because the inputs could not exercise the property.

---

## STOP AND REPORT — a ruling is owed

**Item 18's second half names a behaviour this deployment does not implement.**

§7 already carries one sentence that may be the intended narrowing: *"Acceptance
18 applies to the inbound stream."* Two readings, and ACC does not get to pick:

1. **That sentence is the ruling.** Item 18 is inbound-only, it is implemented,
   covered and now audited, and Part 2 above is a description rather than a gap.
2. **It is not.** The acceptance item is a separately frozen artefact whose text
   names an outbound-waits-for-inbound guarantee, and one sentence inside §7
   narrowing it without saying so is precisely how item 10 came to be frozen
   against something nothing could reach — **AMENDMENT-8's situation exactly**,
   which was ruled a T0 gap and required an amendment rather than a silence.

The difference matters: under reading 2 the gate cannot pass item 18 as written,
and the deferral needs recording the way item 10's was. ACC has built the
checkable assertion either way, so **no work is blocked** — but the gate tally
should not record item 18 as fully green until this is ruled.

Nothing was fixed. `plan_command`'s `required_predecessor_ids` keyword is
production surface, and populating it is a design decision about what causes
what — the class of thing this run stops on rather than invents.

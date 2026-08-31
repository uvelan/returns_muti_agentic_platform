# AMENDMENT-4 — the omc mirror is eventually once, and *never* atomically

**Test:** `backend/tests/acceptance/test_amendment_4_omc_mirror_is_eventually_once.py`
— 2 scenarios, normal suite, **both green**.

Item 17's observable is unchanged by the amendment ("relayed once, correct
record, no duplicate omc write"). The mechanism is not a transaction, and the
amendment says outright what that gives up: **the window between the merge and
the outbox row is explicitly a recoverable gap rather than an impossible one.**
The brief's instruction follows — assert the mirror as *eventually once, never
atomically*, and do not write a scenario that asserts atomicity.

## Both halves, and the second is the one nothing else makes

* **Never atomically.** After a crash between the merge and the mirror, the
  intermediate state is *observable*: the record carries `1Z-AAA` and there is
  **no** mirror row and **no** outbox row. A transaction would make that state
  unrepresentable. A suite that could not tell it from the finished state would
  be equally green against a claim of atomicity production does not implement.
* **Eventually once.** The redelivery completes the sequence — exactly one
  mirror row, exactly one outbox row, the outbox payload naming the mirror row
  actually on file — and a third delivery changes neither, under the same
  `commandId`.

## Against the real mirror

`tests/operations/test_support_message_classification.py`'s mirror tests use
`_RecordingOmc`, which is right for what they assert (the call is made, under
the derived identity, once) and cannot show a crash-safe *order*, having no rows
and no outbox. This builds `DurableOmcMirror` over a real
`OperationalRepository` with both uniqueness constraints in force, and reads the
two collections a production process writes.

**The redelivery is the load-bearing one.** On it the merge writes nothing — the
record already carries the value from the crashed attempt — so it is exactly the
path on which a mirror gated on "did the merge write anything" is skipped, and
skipped permanently. The classification suite covers that gating with the
post-crash state **set up by hand**; this reaches it by actually crashing, which
is the difference between testing the recovery and testing a fixture that
resembles it.

A **control** scenario runs the same artifact with no crash and asserts the same
two rows, so "eventually once" is a claim about convergence rather than about
something ending up in the collections.

## Fault injection — three landed, one discarded

| # | injected fault | result |
| --- | --- | --- |
| INJ-A4a | the mirror gated on `wrote` (the defect the production comment describes) | **1 failed, 1 passed** — the crash scenario reds with no mirror row after redelivery; **the control stays green**, correctly, because `wrote` is true on the clean path |
| INJ-A4b | the outbox `idempotency_key` made fresh per attempt | **2 failed** — the duplicate omc write item 17 forbids |
| INJ-A4c | `_mirror_to_omc` hoisted **above** `persist_binding_decision` (order violated) | **1 failed, 1 passed** — "the merge did not commit before the crash, so this is not the window AMENDMENT-4 describes"; control green |

INJ-A4a's asymmetry is the important one: it proves the scenario is testing the
*recovery* path and not the happy path, which is the whole reason it exists.

**One injection was discarded rather than recorded.** The first attempt at
INJ-A4b replaced the mirror's `$setOnInsert` with `$set`, and the run went red
with `NotImplementedError: the double only upserts with an _id in the filter` —
a failure of the Mongo double, not of the property. No assertion ran. Replaced
with the outbox-key injection above, which reaches the assertions. Second time
on this run that an injection was refused for being red for the wrong reason
(the first was item 10's invalid schema ref).

All injections reverted with `git checkout`; `git status` clean after each. No
production file is modified by this branch.

## Suite

`python -m pytest tests -q` → **5211 passed, 1 failed, 10 skipped, 512
deselected** in 3:56. The failure is the known pre-existing
`test_a_rejected_return_still_opens_no_work_item`. **Zero new failures.**

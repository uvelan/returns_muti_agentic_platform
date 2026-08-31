# Acceptance items 13 and 19 — the reminder cadence, on a desk that closes

**Test:** `backend/tests/acceptance/test_items_13_19_reminder_cadence_in_business_time.py`
— 5 scenarios, normal suite, **9 passed** for the acceptance package.

* **13** — per-case cadence, at most `max_reminders` across N reviews (DR-7),
  and no duplicate reminders across a clarification round-trip.
* **19** — a wait spanning non-business hours fires **no retroactive burst**.

## The gap this closes, stated exactly

`tests/test_support_template_review_gate.py` already drives this loop. Its
`_GateActivities.resolve_business_deadline` is a double:

```python
start = datetime.fromisoformat(request.from_iso)
return ResolvedBusinessDeadline(
    instant_iso=(start + timedelta(seconds=request.working_seconds)).isoformat(),
    calendar_applied=True,
)
```

Wall-clock addition, with **`calendar_applied=True` asserted by the double
rather than computed**. That is the right double for what those tests are about;
it also means that before this module, **no test in the repository ran the
cadence on a calendar that shuts** — a grep for `with_business_calendar` outside
`tests/harness/` returned nothing.

This module keeps that harness — same runtime substitution, same real
`SupportTemplateGateService` over the real `ReviewAggregateStore` — and replaces
exactly one thing: `resolve_business_deadline` is the **real activity**, over a
release carrying the Mon–Fri 09:00–17:00 desk.

## Assert, not assume (dispatch condition 1)

`_assert_business_time_was_actually_used` runs in every business-time scenario
and makes three statements, each catching a different way of proving nothing:

1. `not desk.is_continuous` — a 24/7 calendar satisfies every other assertion
   here by addition;
2. `timings.business_calendar_id == "acceptance-business-hours"` — a calendar
   declared and not selected is a calendar never consulted;
3. `calendar_applied is True` on **every** resolution the run performed, not the
   first. A run does five to nine of these; a fallback taken on one leg would
   leave the early ones true and the late ones silently wall-clock.

## Measured behaviour

Clock starts **Friday 2026-08-28 16:30 America/New_York**, thirty minutes before
a 64-hour close. On the Mon–Fri desk the four reminder legs land at **Monday
10:30, 12:30, 14:30 and 16:30 local**; three reminders fire (the fourth is past
the cap and waits silently), and the deadline — Friday 16:30 + 8 working hours —
is Monday 16:30. Nothing on Saturday, nothing on Sunday, nothing bunched.

A **control** scenario runs the identical wait on the shipped 24/7
`business_calendars.default` and asserts its first reminder lands the same
**Friday evening**. Without it, the weekend assertions could be passing on the
calendar rather than because of it.

## Fault injection — four, each verified to have landed

| # | injected fault | where | result |
| --- | --- | --- | --- |
| INJ-19a | reminder tick computed by wall-clock addition instead of `_business_deadline` | production `_await_template_reviews` | **1 failed, 8 passed** — "a reminder was scheduled for **Friday 18:30** — outside desk hours". The 24/7 control stays green, correctly: it is *about* wall-clock behaviour |
| INJ-13a | cap multiplied by the number of open reviews (a per-review cadence) | production `_await_template_reviews` | **2 failed, 7 passed** — both two-review scenarios red at 4 reminders; every one-review scenario green |
| INJ-13b | a reminder charged on the satisfied-predicate path, not only on timeout | production `_await_template_reviews` | **1 failed, 8 passed** — "waking the wait changed the cadence: 6 reminders with wakes against 3 without" |
| INJ-13c | `desk_configuration` returns the released config unchanged — *the fixture is forgotten* | the test's own fixture | **4 failed, 5 passed** — every business-time scenario reds on `'default' != 'acceptance-business-hours'` |

Each injection was applied by an anchored replace asserting `count(old) == 1`
and **read back at its line numbers** before the run; INJ-19a's diff was checked
to be the two intended lines and no others. Every one reverted with
`git checkout`; `git status` clean after each.

### INJ-13b caught a test that could not fail, and the test was rewritten

This is the finding worth recording. The wake scenario's first form asserted a
**ceiling** — `reminders_sent <= max_reminders` — and its "wake" was a callback
that appended to a counter and left the gate's predicate false. Two independent
defects, both invisible:

* the harness raises `TimeoutError` when the predicate is false, so a wake that
  does not satisfy it is **indistinguishable from a timeout** — the test never
  exercised the wake path at all;
* a ceiling is unfalsifiable here regardless, because the cap clamps the count
  whatever charges it: an implementation spending a leg on every wake exhausts
  the cadence early and still satisfies `<= 3`.

INJ-13b — charging a reminder on the satisfied-predicate path — left **all nine
tests green**. Rewritten as a **comparison**: the same case is run twice,
identical but for the wakes, and the two counts must match; and the wake now
puts a notice for a review the case does not hold into
`pending_template_notices`, which genuinely satisfies the predicate, is drained,
is ignored, and leaves the review open — a clarification round-trip's shape
exactly. The re-run of INJ-13b then failed, with its own message.

`merge.md`'s "green because the inputs can't exercise the property" and "a test
that proves the right thing is asked for, not that the wrong thing is refused",
both found here, in my own instrument, by my own injection.

### The reminder instant is read from the clock, not from the deadlines

An earlier form derived the leg instants from the recorded
`resolve_business_deadline` answers. That instrument does not survive INJ-19a:
an implementation using wall-clock addition stops asking for legs at all, so the
list empties and the failure reads "nothing was resolved" rather than "a
reminder fired on Saturday". The observable is now the runtime clock at the
moment the reminder is logged. That is not the instrument flattening into the
answer — the test never chooses how far the clock moves; the runtime advances by
whatever timeout the gate passes to `wait_condition`, computed from the business
deadline the gate resolved.

## Suite

`python -m pytest tests -q` → **5195 passed, 1 failed, 10 skipped, 512
deselected** in 4:20. The single failure is
`test_a_rejected_return_still_opens_no_work_item`, the known pre-existing one
named in `scripts/ci/known_test_failures.json`. **Zero new failures.**

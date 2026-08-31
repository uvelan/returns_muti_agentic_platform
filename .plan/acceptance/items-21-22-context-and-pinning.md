# Acceptance items 21 and 22 — context across a restart, and the release pin

**Tests:**
`backend/tests/acceptance/test_item_21_context_is_byte_identical_across_a_restart.py` (3)
and `backend/tests/acceptance/test_item_22_the_release_stays_pinned_across_a_promotion.py` (3).
Normal suite, green.

**The gate that runs them** (RV rule 13): no `_real_infra` suffix, no
`live_infra` marker, so `.github/workflows/checks.yml`'s backend job runs both on
every push. Worth stating alongside the standing finding that **CI runs no
live-infra test at all** — `addopts` deselects `live_infra` and `browser`.

---

## Item 21 — byte-identical `assemble_case_context` across kill/restart

`tests/platform/test_case_context_assembly.py` asserts determinism thoroughly:
identical inputs hash the same, input order does not reach the output, ties break
on `factId`, an unknown tokenizer is refused rather than approximated. Twenty-one
tests, and none is duplicated here.

**All of them run in one process**, which is the gap item 21's *"across
kill/restart"* names. Every in-process comparison shares one `PYTHONHASHSEED`, so
anything reached through `str` hash randomisation — a `set` iterated into the
output, a `dict` built from an unordered comparison — is identical on both sides
and invisible. A restart is a new seed.

So the module assembles the same fact log in **two fresh interpreters under
deliberately different `PYTHONHASHSEED` values**, and requires the same hash, the
same payload, the same `consumed_fact_ids` and the same `omitted_fact_ids`. Three
scenarios: a generous budget, a budget that must **evict**, and the tokenizer pin
read from the released configuration rather than typed as a literal.

### Fault injection — and the second one is the whole argument

| # | injected fault | result |
| --- | --- | --- |
| INJ-21a | the canonical projection routed through a `set` — the classic hash-order leak | 2 acceptance tests fail **and** 1 in-slice test fails |
| INJ-21b | output order untouched; only the **eviction tie-break** made `hash()`-dependent | **1 failed, 23 passed** — only `test_the_agreement_holds_where_the_budget_has_to_choose`; **all 21 in-slice determinism tests stay green** |

INJ-21a is the honest half of the report: a crude seed leak also breaks ordering,
so the in-slice suite catches it too, and the new module adds nothing there.

**INJ-21b is what justifies the module.** It preserves the canonical output order
— so every ordering assertion still passes — and makes only the *choice under a
squeeze* seed-dependent. Nothing in the repository sees it except the
cross-process, squeezed-budget scenario. That is item 21's clause demonstrated
rather than argued: the property "the same facts survive a restart" is not
implied by "the same facts survive a second call".

### The squeeze test could not squeeze, and the guard was rewritten

Its first form asserted `len(consumed) < len(_FACTS)` at a budget of 120.
Measured: at 120 **nothing is omitted** — the scoped-latest projection alone
drops two superseded facts, so four of six were kept for a reason that has
nothing to do with the budget, and the guard was green while the test was the
generous-budget case wearing a smaller number. Now asserts a non-empty
`omitted_fact_ids` at a budget of 60, verified to evict exactly one unpinned fact
while the pinned name survives.

**Fifth instrument defect ACC has found in its own work this run**, and the fifth
of one family: green because the inputs could not exercise the property. It was
found by checking what the guard actually measured rather than by trusting the
green.

---

## Item 22 — compaction, and the release pin across a promotion

### The two clauses that were covered, audited rather than duplicated

| # | injected fault | result |
| --- | --- | --- |
| INJ-22c | the pinned-name admission pass disabled | 2 in-slice tests fail (`…pinned_name_survives…`, `…caller_pins_add…`) |
| INJ-22d | omissions no longer recorded in `omitted_fact_ids` | 3 in-slice tests fail, plus the acceptance eviction test |

*Compaction keeps all pinned facts* and *loses none* are both genuinely
load-bearing in-slice. Nothing added.

### The clause that was covered by nothing

*The analysis release stays pinned across a mid-retry config promotion.*
`test_a_crash_between_the_two_stages_resumes_without_reclassifying` rebuilds the
resumed analyser with the **same** release, so it proves reuse and says nothing
about a promotion. Three scenarios added: a promotion between the crash and the
retry; a promotion after both stages are accepted (the redelivery case); and a
**control** — a second event on the same store pinned under a later release —
because every "the pin did not move" assertion is also satisfied by a build that
can never adopt a new release at all.

| # | injected fault | result |
| --- | --- | --- |
| INJ-22a | `pin_routing_decision`'s early-return fast path disabled | **25 passed — MISS** |
| INJ-22b | the fast path **and** the `field: None` CAS filter removed | **2 failed, 23 passed** — both acceptance pin tests; the entire 22-test in-slice classification suite **stays green** |

**INJ-22a is a finding about where the guarantee lives.** The early return
*reads* like the idempotence — it is the branch with the docstring about keeping
the first pin — and removing it changes nothing, because `find_one_and_update`'s
`{… field: None}` filter matches nothing once the field is set and the code
falls through to "the winner's pin is the pin". The fast path is an optimisation;
the CAS is the mechanism. `merge.md`'s *"cite the mechanism that actually fires,
not the one that sounds strongest"* — in production code this time rather than in
a test.

INJ-22b's asymmetry is the other half: with the real mechanism gone, **every
in-slice test still passes.** Before these three scenarios, "a promotion does not
move an accepted stage's pin" was asserted by nothing in the repository.

### An expectation of mine was wrong, and the code was right

The first scenario originally asserted that extraction — a stage that had never
produced a result — would adopt the promoted `release-2`. It records `release-1`.
Reading `pin_routing_decision` rather than reporting a defect showed why: the pin
is taken **before invocation**, so a crash *inside* the invocation leaves the
stage pinned with no accepted result, and the retry routes by the release chosen
for this event rather than by whatever shipped in between. That is a stronger
guarantee than the one expected.

Recorded here rather than quietly amended, because *"the test was adjusted until
it passed"* and *"the test was wrong for a reason someone can check"* look
identical in a diff. **No production defect; a misreading, caught by reading the
source before writing the report.**

---

## Suite

`python -m pytest tests -q` → **5220 passed, 1 failed, 10 skipped, 512
deselected** in 4:44. The failure is the allowlisted pre-existing
`test_a_rejected_return_still_opens_no_work_item`. **Zero new failures.**
All injections reverted with `git checkout`; `git status` clean after each; no
production file is modified by this branch.

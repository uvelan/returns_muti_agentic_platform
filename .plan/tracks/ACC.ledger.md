# ACC ledger

Append-only. One entry per step (contracts.md sect. 3).

**Phase 1 only** — brief items 1, 2 and 7 (harness scaffolding, business-calendar
fixture, fabrication-guard extension). Items 3–6 and 8–10 are acceptance
scenarios over code that does not exist until V3 merges and are deliberately not
attempted here.

Branch `feat/acc-harness`, cut from `e0a5f6c` — the trunk head of
`refactor/unified-return-platform`, `(T0) step:s1-merged pipeline bases
confirmed against approved head`. The brief names `feat/acc-acceptance` off the
post-V3 commit; this phase-1 branch is the orchestrator's direction and is
recorded here as the deviation it is.

Test harness: no venv in this worktree, so the main checkout's
`backend/.venv/Scripts/python.exe` runs with
`PYTHONPATH=<worktree>/backend/src`, from `<worktree>/backend`, with the
gitignored root `.env` copied in (`tests/conftest.py::pytest_configure` requires
it and it is untracked, so it stays untracked here).

---

## step:00 — anchor verification

**Anchors verified (all present at `e0a5f6c`, none adapted):**

| anchor | state |
| --- | --- |
| `backend/src/return_platform/operations/fact_names.py` | present; 2 constants, `SUPPORT_ARTIFACT_AMBIGUOUS` / `SUPPORT_ARTIFACT_UNMATCHED`, both imported by `operations/artifact_binding.py` |
| `backend/tests/test_frozen_modules_gain_no_new_callers.py` | present — backend source-guard shape |
| `frontend/src/domains/returns/ReturnCopilotFabrication.test.ts` | present — frontend source-scan idiom |
| `backend/src/return_platform/operations/business_calendar.py` | present; exports `BusinessCalendar`, `WorkingPeriod`, `advance_business_time`, `is_working_time`, `MAX_HORIZON_DAYS` |
| `configuration/return_configuration.py` `BusinessCalendarConfiguration` / `BusinessWorkingPeriodConfiguration` | present; `ReturnPlatformConfiguration.business_calendars` defaults `()` |
| `backend/tests/conftest.py` `_LIVE_INFRA_MODULE_SUFFIXES = ("_real_infra.py", "_docker.py")` | present (line 48); `_SUITE_MARKERS = ("live_infra", "browser", "integration", "unit")`; `pytest_itemcollected` marks every item |
| `backend/pyproject.toml` | markers `unit/integration/live_infra/browser`; `addopts` carries `-m "not live_infra and not browser"` |
| `scripts/dev/run_real_infra_suite.sh` | present |
| `backend/config/returns/production.yaml` `business_calendars:` | present; `default` is the 24/7 dev calendar (`America/New_York`, every day 0..1440) — cannot exercise business-time behaviour, which is item 2's premise |
| `backend/tests/platform/test_the_normal_suite_never_needs_live_infrastructure.py` | present — AST-scans **every** `.py` under `tests/` except `conftest.py`, so a helper module that constructs a driver must itself be live-classified. Shapes item 1's design. |

No mismatch, no halt.

**Next step:** step:01 — fact-name literal guard (brief item 7).

---

## step:01 — fact-name literal guard (brief item 7)

RV's standing grep (contracts.md sect. 3), made durable.

**Files:** `backend/tests/test_fact_name_literals_live_only_in_fact_names.py` (new).

**Shape.** The vocabulary is *discovered* by importing
`return_platform.operations.fact_names` and reading its public upper-case string
constants — never a copy of the list, which would be a second home for the very
strings the rule keeps in one home, and which would leave a later slice's
constant silently unguarded. Appending a constant there extends this guard in
the same commit.

Scanning is AST rather than text: contracts.md sect. 4 bans the *string
literal*, and prose is not a literal, so docstrings are exempted by node
identity while every other `str` constant is examined — which also catches
shapes a text grep reads past (a name inside `Literal[...]`, a dict key).
`fact_names.py` itself is exempt, resolved from `module.__file__` so moving the
module moves the exemption.

Three tests: the vocabulary is non-empty (a guard over nothing passes forever);
the scanner is proved against a source that *does* carry a literal, and against
the two forms that must stay legal (docstring prose, importing the constant);
and the rule itself over `backend/src/**/*.py`.

**Command:** `python -m pytest tests/test_fact_name_literals_live_only_in_fact_names.py -q`
**Result:** 3 passed.

**What it currently catches: nothing.** The tree is clean — the only occurrences
of either fact name in `backend/src` are lines 19 and 24 of `fact_names.py`, and
`operations/artifact_binding.py` imports both constants. Correct: this guard is
a ratchet against the next slice, not a finding against this one.

**Anchors verified:** `fact_names.py` constants read at runtime (2 discovered);
`test_frozen_modules_gain_no_new_callers.py` source-guard shape followed
(module-level constants, `rglob` over `BACKEND_SRC`, `__pycache__` skipped,
failure message names the sanctioned replacement).

**Next step:** step:02 — Mon–Fri business-calendar fixture (brief item 2).

---

## step:02 — Mon–Fri 09:00–17:00 business-calendar fixture (brief item 2)

**Files (all new):** `backend/tests/harness/__init__.py`,
`backend/tests/harness/business_calendars.py`,
`backend/tests/harness/conftest.py`,
`backend/tests/harness/test_business_hours_calendar_fixture.py`.

**Premise, verified.** `production.yaml`'s `business_calendars.default` declares
every day `0..1440` — `BusinessCalendar.is_continuous` is true, and
`advance_business_time` short-circuits a continuous calendar to
`start + timedelta(seconds=…)`. So against the shipped configuration every
overnight and weekend gap is zero seconds wide and items 13/19 would be
asserting plain addition. Confirmed by reading the file, not assumed.

**Shape.** `nine_to_five_configuration()` returns a
`BusinessCalendarConfiguration` — the configuration model, not the arithmetic's
model, because the real path is workflow → activity →
`ReturnPlatformConfiguration.business_calendars`, and a fixture handing out a
`BusinessCalendar` directly would skip the half of the seam most likely to
break. `as_business_calendar()` converts; `with_business_calendar()` installs a
calendar into a configuration *and* points `return_case.business_calendar_id` at
it (either half alone is a silent no-op — an unnamed calendar is never
consulted, and an id naming nothing falls back to wall clock and logs
`business_calendar_not_configured`, which is legitimate production behaviour and
therefore an invisible way to test nothing). Same-id entries are replaced, not
appended, because `_business_calendar` returns the first match.

Calendar id is `acceptance-business-hours`, deliberately not `default`: a
scenario that meant to install it and did not would otherwise silently get the
24/7 one and pass. Zone is `America/New_York` — the desk's real zone, so the
`fold` / local-wall-clock-day construction stays exercised.

No Mon–Fri constant is imported from anywhere; the pattern is built here,
because `business_calendar.py` deliberately has none.

**Recorded duplication.** `as_business_calendar` re-derives the config→domain
mapping that lives, private, in `ReturnCaseActivities._business_calendar`. Held
to account by `test_the_fixture_maps_to_the_calendar_production_would_build`,
which runs the real activity over a configuration carrying this calendar and
asserts the same instant — so a change to the production mapping fails here
rather than leaving acceptance asserting against a desk the platform does not
have. No production edit made.

**Coverage** (over the pure `operations/business_calendar.py`): overnight gap
(Mon 16:00 + 2h → Tue 10:00); weekend gap (Fri 16:00 + 2h → Mon 10:00);
deadline landing after a weekend with its remainder intact (Fri 16:30 + 8h →
Mon 16:30, the audit scenario); weekend start waits for the opening rather than
bursting (item 19); `is_working_time` at both edges of 09:00/17:00 plus
overnight and weekend; declared holiday; the weekday identity of the dates
themselves, since every expectation is "…because that day is a Saturday".

**Commands:**
- `python -m pytest tests/harness -q` → **10 passed**
- `python -m pytest tests/harness tests/test_fact_name_literals_… -m unit --collect-only` → **13 collected**, i.e. every new test lands in the `unit` suite (none live, none browser)
- `ruff check` / `ruff format` → clean

**Anchors verified:** `BusinessCalendarConfiguration`,
`BusinessWorkingPeriodConfiguration` (weekday 0–6, `end_minute` ≤ 1440),
`ReturnPlatformConfiguration.business_calendars`, `ReturnCaseTimingConfiguration
.business_calendar_id`, `advance_business_time` / `is_working_time` /
`BusinessCalendar.is_continuous`, `ReturnCaseActivities(repository, support_service,
configuration)` + `ResolveBusinessDeadlineInput(from_iso, working_seconds,
business_calendar_id, timezone)` — all as declared, none adapted.

**Next step:** step:03 — kill/restart harness scaffolding (brief item 1).

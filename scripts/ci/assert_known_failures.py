#!/usr/bin/env python3
"""Decide whether a test run that contains failures is nonetheless acceptable.

The repository's suites do not run clean on trunk: one backend test and two
frontend tests fail for reasons that predate this run and are recorded in
`.plan/merge.md`. A CI job that simply demanded exit code 0 would have been red
the moment it merged, and a gate that is red on arrival is a gate people learn
to ignore -- which is exactly how three typecheck errors survived a merge here.

So the suite runs in full, nothing is deselected, nothing is marked skipped, and
this script reads the JUnit report the run produced and compares the set of
failing tests against `known_test_failures.json`:

* a failure that is NOT on the list  -> the job fails (this is the regression
  gate, and it is as strict as an exit-code check);
* a listed test that PASSED          -> the job fails, asking for the line to be
  deleted, so the list cannot rot into a blanket excuse;
* an empty or test-less report       -> the job fails, because a suite that
  collapsed before it collected anything reports zero failures and would
  otherwise read as success.

Exit code 0 means: everything that failed was already failing, everything that
was already failing still fails, and the suite actually ran.

-- The size floor -------------------------------------------------------------

Everything above reasons about the failures IN the report, and that is a hole
wide enough to drive a green CI run through: **neither check can see a test that
never ran.**

The argument for closing it is made from the MECHANISM, not from an incident,
because a guard justified by an anecdote is deleted by the first person who
cannot reproduce the anecdote. Every step below is checkable by reading
`.github/workflows/checks.yml` and this file:

1. Both suite steps in that workflow run under `set +e` and bail out only when
   the status is `-gt 1`. Exit 1 is the TOLERATED path, by design -- the frontend
   suite exits 1 on a correct run because of its two allowlisted failures. A
   truncated run that exits 1 therefore passes that step.
2. Take the three allowlist rules against a run that dropped whole files. Those
   files produced neither failures nor passes, so `unexpected` (failed - allowed)
   and `repaired` (allowed & (ran - failed)) are STRUCTURALLY EMPTY -- not
   unlikely to fire, but incapable of it. `missing` (allowed - ran) is the only
   rule that can fire, and it fires only if a dropped file happened to carry an
   allowlisted id.
3. The one remaining floor is `if not ran`, which catches TOTAL collapse and
   nothing short of it.

So the comparator has exactly one accidental partial guard and one all-or-nothing
guard, and neither asks the question. Drop any set of files carrying no
allowlisted id and this script exits 0 over a fraction of the suite; on a suite
whose allowlist is empty even the accident is unavailable, there being no named
test whose absence could be noticed.

**None of that is a defect in the allowlist.** An allowlist comparator can only
notice failures already on its list. It is the right instrument for "did anything
new break" and the wrong instrument for "did the suite actually run", and until
now nothing in `checks.yml` asked the second question. The floor in
`suite_size_floor.json` is that second question, and it closes (2) precisely
because it does not depend on which files were lost.

The observation that prompted the work is consistent with this and is recorded
as an observation rather than as the load-bearing argument: under memory pressure
`npm test` reported

    Test Files  40 passed (40)

while 21 of 61 files never started -- not "21 failed"; vitest believed there were
forty. It has resisted repetition on some machines and reproduced readily on
others. The hole above is there either way, and the backend has it too: pytest
writes a report of what it ran, and a comparator reading only that report cannot
miss what is not in it.

The distinction it is built around: a suite that ran fewer tests because
somebody deleted some is a CODE CHANGE, and it arrives with a diff to review. A
suite that ran fewer because a worker died is an INFRASTRUCTURE FAILURE
reporting green. The floor makes the second impossible and leaves the first a
one-line, deliberate, reviewable edit -- which is why lowering the floor is a
separate visible act rather than something a shrinking suite does to itself.

Exit code 2, not 1, when the floor is breached: 1 is this script's verdict about
the TESTS, and `checks.yml` reads anything above 1 as the run itself having
failed. A suite that did not run is the second thing, not the first.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _case_id(case: ET.Element) -> str:
    """`<classname>::<name>`, the spelling both reporters can express.

    pytest writes a dotted module path as `classname`; vitest writes the test
    file's path. Neither is ambiguous inside its own suite, and keeping the
    reporter's own words means an id can be copied out of a CI log verbatim.
    """

    classname = case.get("classname") or ""
    name = case.get("name") or ""
    return f"{classname}::{name}" if classname else name


def _read_report(path: Path) -> tuple[set[str], set[str], set[str], int]:
    """`(failed, ran, files, cases)` from one JUnit XML file.

    `cases` is a COUNT of `<testcase>` elements, not `len(ran)`. The two differ:
    `ran` is a set of ids, and a real frontend report measured here held 585
    elements collapsing to 577 distinct ids. The floor counts elements, because
    the question it asks is "how many tests reported", and two tests that happen
    to share a `classname::name` are still two tests -- against `len(ran)` one of
    them could vanish without moving the number.

    `files` is the set of distinct `classname` values -- test FILES for vitest,
    test MODULES for pytest. It is measured alongside the case count because the
    two answer different questions about a short run. A dead worker takes whole
    files with it, so the file count is the number that moves first and moves
    most; the case count is the one that notices a single parametrised family
    quietly failing to expand.
    """

    root = ET.parse(path).getroot()
    failed: set[str] = set()
    ran: set[str] = set()
    files: set[str] = set()
    cases = 0
    for case in root.iter("testcase"):
        cases += 1
        identifier = _case_id(case)
        ran.add(identifier)
        classname = case.get("classname")
        if classname:
            files.add(classname)
        # `failure` is an assertion; `error` is a crash on the way to one. Both
        # mean the test did not pass, and a gate that watched only the first
        # would wave through a collection error.
        if case.find("failure") is not None or case.find("error") is not None:
            failed.add(identifier)
    return failed, ran, files, cases


# How far ABOVE the floor the suite may grow before the floor must be re-staked.
#
# The floor is a floor, not a pin: adding tests never fails this check, and that
# is the whole point -- a recorded expected total would be wrong within a day and
# would train people to edit the number without reading it, which is how the
# allowlist would have rotted had it not been built to self-prune.
#
# But a floor that is never re-staked decays into a floor at zero. Record 867
# today, let the suite reach 2,000 over a year, and a run that executes 900 tests
# -- a collapse worse than the one that motivated this file -- sails through. So
# this borrows `frontend/scripts/check-bundle.js`'s SHRINK_ALLOWANCE exactly: the
# baseline that can only ever move one way is the baseline that rots, so a large
# enough move in the good direction fails too, and prints the number to write.
#
# 25% is deliberately loose. The growth allowance in the bundle ratchet is 0.5%
# because it is absorbing zlib noise; there is no noise here (these are integer
# counts, and collection is platform-stable in this repository -- every skipif in
# `backend/tests` is a RUNTIME skip, so a skipped test is still collected and
# still writes a `<testcase>`). This number is not absorbing measurement error,
# it is choosing how often a human is asked to look. A quarter of the suite is
# rare enough to be a real event and small enough that the floor never trails the
# suite by the factor that would make it meaningless.
RESTAKE_ALLOWANCE = 0.25


def _check_size(suite: str, floor_path: Path, cases: int, files: set[str]) -> int:
    """0 if the run is big enough to be believed, 2 if it is not.

    2 rather than 1 on purpose. 1 is this script's verdict about the tests, and
    `checks.yml` discriminates on exactly that boundary -- "anything else is the
    run itself breaking, and no allowlist covers that". A suite two thirds of
    which never started is the run breaking.
    """

    if not floor_path.exists():
        print(
            f"::error::no size floor at {floor_path} -- there is nothing to measure "
            "this run against. A suite check with no recorded floor is not a check."
        )
        return 2

    # Everything about reading this file is defended, because the alternative is
    # an uncaught exception -- and an uncaught exception exits 1, which is the one
    # code this script uses to mean "a test failed". A malformed floor file would
    # then be filed as a test failure: the exact misclassification the whole
    # exit-code discipline here exists to prevent, arriving through the guard that
    # was added to enforce it.
    try:
        document = json.loads(floor_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"::error::{floor_path} could not be read as JSON: {error}")
        return 2

    suites = document.get("suites") if isinstance(document, dict) else None
    recorded = suites.get(suite) if isinstance(suites, dict) else None
    if not isinstance(recorded, dict):
        print(
            f"::error::no usable floor for suite {suite!r} in {floor_path} -- record one "
            "before gating it, or the job cannot tell a full run from a collapsed one."
        )
        return 2

    measured = {"cases": cases, "files": len(files)}
    failed = False

    # "distinct JUnit classnames" rather than "test files" because that is what
    # is actually counted and the two are not the same on both suites: vitest
    # writes the test file's path, so one classname is one file, while pytest
    # writes a dotted module path AND appends the test class where there is one,
    # so a module holding two `Test*` classes contributes three. That is fine for
    # a floor -- it is stable run to run, and it still moves when a worker dies --
    # but the message must not claim to be counting files on a suite where it is
    # not.
    for key, label in (("cases", "test cases"), ("files", "distinct test files/modules")):
        baseline = recorded.get(key)
        # Not a truthiness test: 0 is falsy and would read as "absent", and a
        # floor of 0 is a floor that cannot fail and must be rejected out loud.
        if not isinstance(baseline, int) or isinstance(baseline, bool) or baseline <= 0:
            print(f"::error::{floor_path} has no usable {suite}.{key} floor")
            failed = True
            continue

        count = measured[key]
        if count < baseline:
            print(
                f"::error::THE SUITE SHRANK: {count} {label} reported, "
                f"but the recorded floor is {baseline} -- {baseline - count} did not report.\n"
                "   Nothing in this report says they FAILED. They are simply absent, and a\n"
                "   report cannot fail a test it does not contain, which is why this check\n"
                "   exists and why the exit code is not 1.\n"
                "   If a worker died or the runner ran out of memory, this is an\n"
                "   infrastructure failure that was about to report green: re-run it.\n"
                "   If tests were deliberately removed, lower the floor in the SAME commit\n"
                f'   that removes them, in scripts/ci/suite_size_floor.json:  "{key}": {count}'
            )
            failed = True
        elif count > baseline * (1 + RESTAKE_ALLOWANCE):
            print(
                f"::error::the floor has fallen behind: {count} {label} ran against a "
                f"recorded floor of {baseline}.\n"
                "   This is not a complaint about the suite -- it grew, which is good. It is\n"
                "   that a floor this far below the suite no longer catches anything: a run\n"
                f"   could lose {count - baseline} {label} and still clear it. Re-stake it:\n"
                f'     "{key}": {count}'
            )
            failed = True

    if failed:
        return 2

    print(
        f"suite size held: {measured['files']} test files/modules, "
        f"{measured['cases']} test cases "
        f"(floor {recorded['files']} / {recorded['cases']})"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, help="key under `suites` in the allowlist")
    parser.add_argument(
        "--report",
        required=True,
        type=Path,
        action="append",
        dest="reports",
        help="JUnit XML written by the run; repeatable",
    )
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=Path(__file__).with_name("known_test_failures.json"),
    )
    parser.add_argument(
        "--floor",
        type=Path,
        default=Path(__file__).with_name("suite_size_floor.json"),
        help="recorded minimum size of this suite; see that file's $comment",
    )
    arguments = parser.parse_args()

    document = json.loads(arguments.allowlist.read_text(encoding="utf-8"))
    suites = document.get("suites", {})
    if arguments.suite not in suites:
        print(f"::error::no suite named {arguments.suite!r} in {arguments.allowlist}")
        return 2
    allowed = set(suites[arguments.suite].get("known_failures", []))

    failed: set[str] = set()
    ran: set[str] = set()
    files: set[str] = set()
    cases = 0
    for report in arguments.reports:
        if not report.exists():
            print(f"::error::no JUnit report at {report} -- the run did not produce one")
            return 2
        report_failed, report_ran, report_files, report_cases = _read_report(report)
        failed |= report_failed
        ran |= report_ran
        files |= report_files
        cases += report_cases

    if not ran:
        print("::error::the report contains no test cases; treating that as a failed run")
        return 2

    # Deliberately BEFORE the allowlist verdict and independent of it. The
    # frontend suite exits non-zero on a correct run -- two allowlisted failures
    # in src/domains/registry.test.ts -- so a size check that only ran on success
    # would be gated by the very condition it exists to doubt. It has to be able
    # to say "this run was too small to believe" about a red run just as readily
    # as about a green one.
    size = _check_size(arguments.suite, arguments.floor, cases, files)

    unexpected = sorted(failed - allowed)
    repaired = sorted(allowed & (ran - failed))
    missing = sorted(allowed - ran)

    print(f"{len(ran)} tests ran, {len(failed)} failed, {len(allowed)} allowlisted")

    for identifier in unexpected:
        print(f"::error::NEW FAILURE (not on the allowlist): {identifier}")
    for identifier in repaired:
        print(
            "::error::allowlisted test now PASSES -- delete it from "
            f"scripts/ci/known_test_failures.json: {identifier}"
        )
    for identifier in missing:
        # Renamed, moved, or no longer collected. Silence here would let a
        # deleted test keep an allowlist entry alive for a future namesake.
        print(
            "::error::allowlisted test was not collected (renamed or removed?) -- "
            f"update scripts/ci/known_test_failures.json: {identifier}"
        )

    # A short run outranks a clean verdict about what it happened to contain: if
    # the suite did not run, the allowlist's opinion of it is not evidence. So 2
    # wins over 1, and over 0.
    if size != 0:
        return size

    if unexpected or repaired or missing:
        return 1

    print(f"only the {len(allowed)} known, still-failing tests failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

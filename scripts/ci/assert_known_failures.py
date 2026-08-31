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


def _read_report(path: Path) -> tuple[set[str], set[str]]:
    """`(failed, ran)` from one JUnit XML file."""

    root = ET.parse(path).getroot()
    failed: set[str] = set()
    ran: set[str] = set()
    for case in root.iter("testcase"):
        identifier = _case_id(case)
        ran.add(identifier)
        # `failure` is an assertion; `error` is a crash on the way to one. Both
        # mean the test did not pass, and a gate that watched only the first
        # would wave through a collection error.
        if case.find("failure") is not None or case.find("error") is not None:
            failed.add(identifier)
    return failed, ran


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
    arguments = parser.parse_args()

    document = json.loads(arguments.allowlist.read_text(encoding="utf-8"))
    suites = document.get("suites", {})
    if arguments.suite not in suites:
        print(f"::error::no suite named {arguments.suite!r} in {arguments.allowlist}")
        return 2
    allowed = set(suites[arguments.suite].get("known_failures", []))

    failed: set[str] = set()
    ran: set[str] = set()
    for report in arguments.reports:
        if not report.exists():
            print(f"::error::no JUnit report at {report} -- the run did not produce one")
            return 2
        report_failed, report_ran = _read_report(report)
        failed |= report_failed
        ran |= report_ran

    if not ran:
        print("::error::the report contains no test cases; treating that as a failed run")
        return 2

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

    if unexpected or repaired or missing:
        return 1

    print(f"only the {len(allowed)} known, still-failing tests failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

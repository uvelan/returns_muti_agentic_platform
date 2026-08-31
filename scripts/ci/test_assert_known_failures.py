#!/usr/bin/env python3
"""Negative-control tests for assert_known_failures.py.

`checks.yml` calls this comparator on every run, and on trunk the comparator
says "green" while three tests are failing. That verdict is worth nothing unless
the comparator can still say red -- an allowlist that has only ever been run
against the runs it accepts is an allowlist nobody has tested, and a broken one
would turn the whole gate into a very expensive `exit 0`.

So this plants reports the comparator MUST reject: a failure that is not on the
list, a listed test that has started passing, a listed test that has vanished,
and a run that collected nothing at all.

It also plants the reports the SIZE FLOOR must reject. That half is here for a
sharper reason than symmetry. The floor's entire job is to disbelieve a report
that looks fine, so unlike the allowlist it has no failing test to point at and
no red run to notice it in -- a floor wired up wrongly is indistinguishable, on
every real run, from a floor working perfectly. The only thing that can tell
those apart is a planted short run, which is what the second half of this file
is. A size check with no negative control is precisely the shape of gate this
repository already knows not to ship.

No pytest, no dependencies -- it runs on a bare runner and on a developer laptop
identically:

    python scripts/ci/test_assert_known_failures.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

COMPARATOR = Path(__file__).resolve().parent / "assert_known_failures.py"

ALLOWLIST = {
    "suites": {
        "demo": {
            "known_failures": [
                "tests.test_thing::test_known_broken",
                "src/a.test.ts::a suite > a known broken case",
            ]
        }
    }
}


def _report(cases: list[tuple[str, str, str | None]]) -> str:
    """`(classname, name, outcome)` where outcome is None, "failure" or "error"."""

    body = []
    for classname, name, outcome in cases:
        attributes = f'classname="{classname}" name="{name}"'
        if outcome is None:
            body.append(f"    <testcase {attributes} />")
        else:
            body.append(
                f"    <testcase {attributes}>"
                f'<{outcome} message="planted">detail</{outcome}>'
                "</testcase>"
            )
    joined = "\n".join(body)
    return f'<?xml version="1.0" encoding="utf-8"?>\n<testsuite name="planted">\n{joined}\n</testsuite>\n'


KNOWN_BACKEND = ("tests.test_thing", "test_known_broken")
KNOWN_FRONTEND = ("src/a.test.ts", "a suite > a known broken case")
HEALTHY = ("tests.test_thing", "test_fine")

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "ok  " if condition else "FAIL"
    print(f"  [{status}] {label}{(' -- ' + detail) if detail and not condition else ''}")
    if not condition:
        failures.append(label)


# The size of the standard planted report below: three cases across two
# classnames. Controls that plant something smaller pass their own floor, so that
# each one states the size it is being judged against rather than inheriting it.
STANDARD_FLOOR = {"cases": 3, "files": 2}

_floor_serial = 0


def run(
    allowlist: Path,
    report: Path | str,
    floor: object = STANDARD_FLOOR,
    root: Path | None = None,
) -> tuple[int, str]:
    """Run the comparator. `floor` is the recorded size, or a path, or None.

    `None` means "point --floor at a file that does not exist", which is its own
    negative control: a gate whose baseline has been deleted must fail, not pass.
    """

    global _floor_serial
    if isinstance(floor, Path):
        floor_path = floor
    elif floor is None:
        floor_path = (root or Path(str(report)).parent) / "no-such-floor.json"
    else:
        _floor_serial += 1
        floor_path = (root or Path(str(report)).parent) / f"floor-{_floor_serial}.json"
        floor_path.write_text(json.dumps({"suites": {"demo": floor}}), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(COMPARATOR),
            "--suite",
            "demo",
            "--allowlist",
            str(allowlist),
            "--floor",
            str(floor_path),
            "--report",
            str(report),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return result.returncode, result.stdout.decode("utf-8", "replace")


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        allowlist = root / "allowlist.json"
        allowlist.write_text(json.dumps(ALLOWLIST), encoding="utf-8")

        def write(name: str, cases: list[tuple[str, str, str | None]]) -> Path:
            path = root / name
            path.write_text(_report(cases), encoding="utf-8")
            return path

        print("the accepted run: exactly the known failures, and they still fail")
        code, out = run(
            allowlist,
            write(
                "green.xml",
                [
                    (*KNOWN_BACKEND, "failure"),
                    (*KNOWN_FRONTEND, "failure"),
                    (*HEALTHY, None),
                ],
            ),
        )
        check("accepts a run whose only failures are allowlisted", code == 0, out)

        print("\nthe regression it exists to catch")
        code, out = run(
            allowlist,
            write(
                "regression.xml",
                [
                    (*KNOWN_BACKEND, "failure"),
                    (*KNOWN_FRONTEND, "failure"),
                    (*HEALTHY, "failure"),
                ],
            ),
        )
        check("rejects a failure that is not on the allowlist", code == 1, out)
        check("names the new failure", "test_fine" in out, out)

        print("\nan error is a failure too -- a crash on the way to an assertion")
        code, out = run(
            allowlist,
            write(
                "errored.xml",
                [
                    (*KNOWN_BACKEND, "failure"),
                    (*KNOWN_FRONTEND, "failure"),
                    (*HEALTHY, "error"),
                ],
            ),
        )
        check("rejects an errored test that is not on the allowlist", code == 1, out)

        print("\nthe list must not rot")
        code, out = run(
            allowlist,
            write(
                "repaired.xml",
                [
                    (*KNOWN_BACKEND, None),
                    (*KNOWN_FRONTEND, "failure"),
                    (*HEALTHY, None),
                ],
            ),
        )
        check("rejects a run where an allowlisted test now passes", code == 1, out)
        check("asks for the stale line to be deleted", "delete it from" in out, out)

        code, out = run(
            allowlist,
            write(
                "vanished.xml",
                [
                    (*KNOWN_FRONTEND, "failure"),
                    (*HEALTHY, None),
                ],
            ),
            # Two cases, not three: this control deliberately drops one, so it is
            # judged against a floor of its own rather than tripping the size
            # check and answering 2 where this assertion wants 1.
            floor={"cases": 2, "files": 2},
        )
        check("rejects a run that never collected an allowlisted test", code == 1, out)

        print("\na suite that collapsed reports no failures, and must not read as success")
        code, out = run(allowlist, write("empty.xml", []))
        check("rejects a report containing no test cases", code == 2, out)

        code, out = run(allowlist, root / "does-not-exist.xml")
        check("rejects a missing report", code == 2, out)

        # ------------------------------------------------------------------
        # The size floor.
        #
        # `empty.xml` above is the only shrinkage the comparator used to catch,
        # and it catches it by being TOTAL. Everything below is the case that
        # actually happened: a suite that came back partial, reported every test
        # it ran as passing, and was internally consistent about it.
        # ------------------------------------------------------------------
        print("\na suite that came back SMALLER must not read as success")

        def suite_of(files: int, cases_per_file: int, outcome: str | None = None):
            """A plausible report: `files` files, `cases_per_file` tests in each."""

            return [
                (f"src/f{f}.test.ts", f"suite {f} > case {c}", outcome)
                for f in range(files)
                for c in range(cases_per_file)
            ]

        # The shape of the observed defect, in miniature: the recorded suite is
        # 10 files / 50 cases, the runner brings back 6 files / 30 cases, and
        # every one of the 30 PASSES. There is not a single failure to point at.
        full = write("size-full.xml", suite_of(10, 5))
        truncated = write("size-truncated.xml", suite_of(6, 5))
        floor = {"cases": 50, "files": 10}
        empty_allowlist = root / "empty-allowlist.json"
        empty_allowlist.write_text(
            json.dumps({"suites": {"demo": {"known_failures": []}}}), encoding="utf-8"
        )

        code, out = run(empty_allowlist, full, floor=floor)
        check("accepts a run that is the size it should be", code == 0, out)
        check("says so in the log", "suite size held" in out, out)

        code, out = run(empty_allowlist, truncated, floor=floor)
        check("REJECTS an all-green run that is missing a fifth of its files", code != 0, out)
        check("names the shortfall in cases (50 recorded, 30 ran)", "20 did not report" in out, out)
        check("names the shortfall in files (10 recorded, 6 ran)", "4 did not report" in out, out)
        check("says the suite shrank", "THE SUITE SHRANK" in out, out)
        # 2, not 1. `checks.yml` reads >1 as "the run failed, not the tests", and
        # a suite that did not run is exactly that. Reporting 1 would file an
        # infrastructure failure under "a test is failing", which is the wrong
        # queue and the wrong owner.
        check("exits 2 (the run broke), not 1 (a test failed)", code == 2, out)

        # The condition the frontend suite is in every single day: legitimately
        # red AND short. The allowlist would rule "acceptable" on the failures it
        # can see; the floor has to override that, or a size check would be gated
        # by the very condition it exists to doubt.
        red_and_short = write(
            "size-red-and-short.xml",
            [*suite_of(6, 5), (*KNOWN_BACKEND, "failure"), (*KNOWN_FRONTEND, "failure")],
        )
        code, out = run(allowlist, red_and_short, floor={"cases": 52, "files": 12})
        check("rejects a run that is short AND legitimately red", code == 2, out)
        check("the shortfall outranks the allowlist's verdict", "THE SUITE SHRANK" in out, out)

        # A single missing file, not a collapse. The floor has no slack by design:
        # these are integer counts, not gzip bytes, and there is no measurement
        # noise for an allowance to absorb -- so any slack would be a hole of
        # exactly that size for tests to disappear into.
        code, out = run(empty_allowlist, write("size-one-short.xml", suite_of(9, 5)), floor=floor)
        check("rejects a run missing a single file", code == 2, out)

        # The floor counts `<testcase>` ELEMENTS, not distinct ids. A real
        # frontend report measured during this work held 585 elements collapsing
        # to 577 distinct `classname::name` ids, so the two measures genuinely
        # differ here. Against distinct ids, one of a duplicated pair could stop
        # running without moving the number -- a blind spot the size of every
        # duplicated name in the suite.
        duplicated = write(
            "size-duplicate-ids.xml",
            [("src/f0.test.ts", "same name", None)] * 10 + suite_of(9, 5),
        )
        halved = write(
            "size-duplicate-ids-halved.xml",
            [("src/f0.test.ts", "same name", None)] * 5 + suite_of(9, 5),
        )
        # 9 files, not 10: the duplicate block reuses `src/f0.test.ts`, which
        # `suite_of(9, ...)` also emits. 55 elements collapsing to 46 ids.
        dup_floor = {"cases": 55, "files": 9}
        code, out = run(empty_allowlist, duplicated, floor=dup_floor)
        check("counts elements, not distinct ids (55 elements, 46 ids)", code == 0, out)
        code, out = run(empty_allowlist, halved, floor=dup_floor)
        check("catches five duplicate-named tests vanishing", code == 2, out)

        print("\nbut it is a FLOOR, not a pin -- growth is not a failure")
        code, out = run(empty_allowlist, write("size-grown.xml", suite_of(11, 5)), floor=floor)
        check("accepts a suite that grew (55 cases against a floor of 50)", code == 0, out)

        print("\nand a floor the suite has outgrown is a floor at zero")
        code, out = run(empty_allowlist, write("size-way-up.xml", suite_of(20, 5)), floor=floor)
        check("rejects a floor the suite has left far behind", code == 2, out)
        check("prints the number to re-stake it at", '"cases": 100' in out, out)

        print("\na floor that cannot fail is not a floor")
        code, out = run(empty_allowlist, full, floor=None)
        check("rejects a missing floor file", code == 2, out)
        check("says a check with no floor is not a check", "is not a check" in out, out)

        code, out = run(empty_allowlist, full, floor={"cases": 0, "files": 10})
        check("rejects a floor of zero", code == 2, out)

        code, out = run(empty_allowlist, full, floor={"files": 10})
        check("rejects a floor with a missing count", code == 2, out)

        code, out = run(empty_allowlist, full, floor={"cases": "50", "files": 10})
        check("rejects a floor that is not a number", code == 2, out)

        no_suite = root / "floor-no-suite.json"
        no_suite.write_text(json.dumps({"suites": {"other": {"cases": 1, "files": 1}}}), "utf-8")
        code, out = run(empty_allowlist, full, floor=no_suite)
        check("rejects a suite gated with no floor recorded for it", code == 2, out)

        # A malformed floor file must exit 2 like every other "cannot judge this
        # run" condition. If it escaped as an uncaught exception Python would exit
        # 1 -- the code this script uses for "a test failed" -- and a typo in a
        # JSON file would be filed as a failing test, which is precisely the
        # misclassification the exit codes here exist to prevent.
        broken = root / "floor-broken.json"
        broken.write_text("{not json", encoding="utf-8")
        code, out = run(empty_allowlist, full, floor=broken)
        check("rejects an unparseable floor file with 2, not a traceback", code == 2, out)
        check("no traceback escaped", "Traceback" not in out, out)

        code, out = run(empty_allowlist, full, floor=42)
        check("rejects a floor entry that is not an object", code == 2, out)

        wrong_root = root / "floor-wrong-root.json"
        wrong_root.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        code, out = run(empty_allowlist, full, floor=wrong_root)
        check("rejects a floor file whose root is not an object", code == 2, out)

    print()
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        return 1
    print("all negative controls passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

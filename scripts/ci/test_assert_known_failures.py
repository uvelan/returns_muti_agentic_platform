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


def run(allowlist: Path, report: Path | str) -> tuple[int, str]:
    result = subprocess.run(
        [
            sys.executable,
            str(COMPARATOR),
            "--suite",
            "demo",
            "--allowlist",
            str(allowlist),
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
        )
        check("rejects a run that never collected an allowlisted test", code == 1, out)

        print("\na suite that collapsed reports no failures, and must not read as success")
        code, out = run(allowlist, write("empty.xml", []))
        check("rejects a report containing no test cases", code == 2, out)

        code, out = run(allowlist, root / "does-not-exist.xml")
        check("rejects a missing report", code == 2, out)

    print()
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        return 1
    print("all negative controls passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

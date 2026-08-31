"""RV rule 13, applied to ACC's own kill primitive: something must run the proof.

`tests/harness/posix_signal_proof.py` proves the one link RV narrowed in the
chaos harness — whether `os.killpg(os.getpgid(pid), SIGTERM)` reaches the child
through the session `WorkerProcess.start()` establishes — plus the body of the
behavioural pin that `skipif(os.name == "nt")` hides on this run's dev platform.
It was written deliberately uncollectable (not `test_*`) so pytest could not
silently skip it on Windows, and it was run **once, by hand, in a container**.

That made it exactly what rule 13 names: **a guard with no gate.** Correct in
what it proved, invoked by nobody, on the branch whose whole subject is guards
nothing runs. This module is the gate.

**Why running the script rather than porting its four checks into tests here.**
The script imports only the standard library plus
`tests.harness.chaos_restart`, which is what lets it run in a bare
`python:3.13-slim` with no install; four pytest tests would drag `conftest.py`'s
`return_platform` import in and lose that property, and the container run is
still the right tool on a Windows dev machine. Running it as a subprocess keeps
one implementation of the proof and gives it two callers.

**What this changes about the residual risk, which the branch's own record had
wrong in both directions.** `.github/workflows/checks.yml` runs every job on
**`ubuntu-latest`**. So:

* the behavioural pin
  `test_chaos_restart.py::test_stop_lets_the_worker_handle_its_signal_and_kill_does_not`
  is **not** an ungated guard at all — it is skipped on the dev machine and
  **executed on every push**. The record's "it has never run" was true of this
  workstation and false of the pipeline, which *understated* the coverage;
* and this module is genuinely gated for the same reason. `skipif` on Windows is
  not the shape ACC criticised — "skipped on the platform that runs it" — because
  the platform that runs it is Linux. The criticism applies to a guard whose
  *only* runner skips it; here the only runner is the one that executes it.

The dev-machine skip is therefore a convenience, not a hole, and the message
says so rather than implying a gap that does not exist.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2]
_PROOF = _BACKEND / "tests" / "harness" / "posix_signal_proof.py"

#: Four `WorkerProcess` launches with settle times, plus a 300s idle child that
#: is killed rather than waited on. Comfortably above the ~6s it takes, and low
#: enough that a hang fails the job instead of burning the CI timeout.
_BUDGET_SECONDS = 180


def test_the_proof_script_is_where_this_module_expects_it() -> None:
    """A missing file must fail loudly, not skip.

    Without this the module below would be one rename away from being a test
    that silently passes on Windows and errors on Linux -- and the Windows half
    is the half a developer sees.
    """
    assert _PROOF.is_file(), (
        f"{_PROOF} is gone. The chaos harness's session/killpg link is proved "
        "nowhere else, and the behavioural stop/kill pin it doubles is Windows-"
        "skipped."
    )


@pytest.mark.skipif(
    os.name == "nt",
    reason=(
        "There is no SIGTERM to deliver and no process group to signal on Windows. "
        "This is not the 'skipped on the platform that runs it' shape: CI runs on "
        "ubuntu-latest, so the pipeline executes this and only the dev workstation "
        "skips it. On Windows, run it by hand: docker run --rm -v <repo>/backend:/w "
        "-w /w python:3.13-slim python tests/harness/posix_signal_proof.py"
    ),
)
def test_the_session_and_signal_links_still_hold() -> None:
    """All four links, executed, with the failing one named.

    The script prints one line per check and exits non-zero if any fails, so the
    assertion carries its own diagnosis: `FAIL  killpg(...) reaches the
    grandchild through the session` says which link broke, where "the harness is
    wrong" would not.
    """
    completed = subprocess.run(  # noqa: S603 - argv is built here
        [sys.executable, str(_PROOF)],
        capture_output=True,
        text=True,
        cwd=str(_BACKEND),
        check=False,
        timeout=_BUDGET_SECONDS,
    )

    # A refusal is exit 2 and means the proof declined to run, which on a POSIX
    # runner would be a bug in the proof rather than a passing check.
    assert completed.returncode != 2, (
        "the proof refused to run on a platform this module believes is POSIX:\n"
        f"{completed.stdout}\n{completed.stderr}"
    )
    assert completed.returncode == 0, (
        "the chaos harness's kill primitives no longer hold, and every kill and "
        "restart scenario rests on them:\n"
        f"{completed.stdout}\n{completed.stderr}"
    )
    # Not just exit 0: a script that stopped running its checks would also exit
    # 0. The four PASS lines are the evidence, counted.
    assert completed.stdout.count("PASS  ") == 4, (
        "the proof exited cleanly without reporting four passing checks, so it "
        f"proved less than it claims:\n{completed.stdout}"
    )
    assert "all four links proved" in completed.stdout

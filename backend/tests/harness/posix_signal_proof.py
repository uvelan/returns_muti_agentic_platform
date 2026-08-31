"""The one unproven link in the harness, proved on a platform that has it.

ACC phase 2, dispatch condition 1(b). RV narrowed the SIGTERM pin's single
unproven link to precisely this: whether
`os.killpg(os.getpgid(pid), SIGTERM)` reaches the child through the session
`WorkerProcess.start()` establishes with `start_new_session=True`. Everything
upstream -- spec construction, script generation, launch, `stop()`, `kill()` --
was proven to execute in phase 1. The behavioural pin
(`test_chaos_restart.py::test_stop_lets_the_worker_handle_its_signal_and_kill_does_not`)
is `skipif(os.name == "nt")`, and this run's dev platform is Windows, so on the
machine the suite actually runs on that assertion has never executed once.

**Why a script and not a test.** `tests/conftest.py` imports `return_platform`,
so collecting a single harness test inside a Linux container would require the
whole dependency tree installed there. `tests/harness/chaos_restart.py` imports
nothing but the standard library, so this file can exercise it under a bare
`python:3.13-slim` with no install at all. Run it as:

    docker run --rm -v <repo>/backend:/w -w /w python:3.13-slim \
        python tests/harness/posix_signal_proof.py

It is deliberately not named `test_*`: pytest must not collect it, because on
Windows every check here is vacuous and a silently-skipped proof is exactly the
shape ("skipped on the platform that runs it") this run keeps finding.

**What it proves, in four checks.**

1. `start_new_session=True` really does put the child in its own process group
   whose id equals its pid -- the premise `os.getpgid(pid)` depends on.
2. `killpg(getpgid(pid), SIGTERM)` reaches a *grandchild* that the worker
   spawned. This is the link RV named, and the one that matters: signalling the
   parent alone leaves a child holding a task queue.
3. `stop()` lets a worker run its `SIGTERM` handler (the drain path).
4. `kill()` does not (the crash path).

Checks 3 and 4 are the body of the Windows-skipped test, executed. Exit status
is 0 only if all four hold; each prints its own line so a partial failure names
which link broke rather than reporting "the harness is wrong".
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.harness.chaos_restart import WorkerProcess, WorkerSpec  # noqa: E402

_FAILURES: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"PASS  {name}")
        return
    _FAILURES.append(name)
    print(f"FAIL  {name}{(' -- ' + detail) if detail else ''}")


def _spawner_script(directory: Path, heartbeat: Path) -> Path:
    """A parent that spawns a child which heartbeats to a file, then idles.

    The heartbeat is the observable: a grandchild that survives the signal keeps
    writing, and a stale mtime is how "the tree was reached" is measured without
    asking the OS whether a pid is alive -- which on POSIX answers yes for a
    zombie that has been reaped by nobody.
    """
    child = directory / "child.py"
    child.write_text(
        "import time, sys\n"
        "while True:\n"
        f"    open({str(heartbeat)!r}, 'w').write(str(time.time()))\n"
        "    time.sleep(0.1)\n",
        encoding="utf-8",
    )
    parent = directory / "parent.py"
    parent.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, {str(child)!r}])\n"
        "time.sleep(300)\n",
        encoding="utf-8",
    )
    return parent


def _graceful_script(directory: Path, marker: Path) -> Path:
    script = directory / "graceful.py"
    script.write_text(
        "import signal, sys, time\n"
        "def _drain(*_):\n"
        f"    open({str(marker)!r}, 'w').write('drained')\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, _drain)\n"
        "time.sleep(300)\n",
        encoding="utf-8",
    )
    return script


def check_session_gives_the_child_its_own_group(directory: Path) -> None:
    """`getpgid(pid) == pid` -- the premise `killpg` is aimed at."""
    script = directory / "idle.py"
    script.write_text("import time\ntime.sleep(300)\n", encoding="utf-8")
    worker = WorkerProcess(WorkerSpec(name="idle", argv=(sys.executable, str(script))))
    try:
        worker.start()
        time.sleep(0.5)
        pid = worker.pid
        assert pid is not None
        group = os.getpgid(pid)
        _check(
            "start_new_session puts the worker in its own process group",
            group == pid,
            f"getpgid({pid}) == {group}; a group id that is not the pid means killpg "
            "would signal the suite's own group, not the worker's",
        )
    finally:
        worker.kill()


def check_killpg_sigterm_reaches_the_grandchild(directory: Path) -> None:
    """The link RV named: does the polite signal traverse the session?"""
    heartbeat = directory / "heartbeat"
    parent = _spawner_script(directory, heartbeat)
    worker = WorkerProcess(WorkerSpec(name="spawner", argv=(sys.executable, str(parent))))
    try:
        worker.start()
        deadline = time.monotonic() + 10.0
        while not heartbeat.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not heartbeat.exists():
            _check("the grandchild started at all", False, "no heartbeat before the signal")
            return

        pid = worker.pid
        assert pid is not None
        group = os.getpgid(pid)

        # The scoping half, and it is not decoration. Measured: with
        # `start_new_session=True` removed, the worker inherits the runner's
        # group, `killpg` signals *everything*, the grandchild stops anyway --
        # and a check that only watched the heartbeat reported PASS. That is
        # "green because the inputs cannot exercise the property": the signal
        # reaching the child proves nothing if it reached the child by reaching
        # the whole world. So the target group must be the worker's own before
        # the heartbeat means anything.
        if group == os.getpgid(0):
            _check(
                "killpg(getpgid(pid), SIGTERM) reaches the grandchild through the session",
                False,
                f"the worker shares the runner's process group ({group}) -- killpg here "
                "signals the suite itself, so any observed death is collateral rather than "
                "the session doing its job",
            )
            return

        os.killpg(group, signal.SIGTERM)
        time.sleep(1.5)
        settled = heartbeat.stat().st_mtime
        time.sleep(1.5)
        _check(
            "killpg(getpgid(pid), SIGTERM) reaches the grandchild through the session",
            heartbeat.stat().st_mtime == settled,
            "the heartbeat kept moving after the group signal -- the child outlived it, "
            "which is a worker still polling a task queue after the scenario believes it is gone",
        )
    finally:
        worker.kill()


def check_stop_drains_and_kill_does_not(directory: Path) -> None:
    """The body of the Windows-skipped behavioural pin, executed."""
    marker = directory / "handled-sigterm"
    script = _graceful_script(directory, marker)
    spec = WorkerSpec(name="graceful", argv=(sys.executable, str(script)))

    polite = WorkerProcess(spec)
    try:
        polite.start()
        time.sleep(1.0)
        polite.stop()
    finally:
        polite.kill()
    _check(
        "stop() gives the worker its SIGTERM handler (the drain path)",
        marker.exists(),
        "no drain marker -- stop() forced without asking, so every POSIX teardown is a SIGKILL",
    )

    if marker.exists():
        marker.unlink()
    violent = WorkerProcess(spec)
    try:
        violent.start()
        time.sleep(1.0)
        violent.kill()
    finally:
        violent.kill()
    time.sleep(0.5)
    _check(
        "kill() does not run the worker's SIGTERM handler (the crash path)",
        not marker.exists(),
        "a drain marker after kill() -- the durability scenarios would be exercising the "
        "drain path while claiming to test unplanned loss, and would pass",
    )


def main() -> int:
    if os.name == "nt":
        print(
            "REFUSED  this proof is vacuous on Windows: there is no SIGTERM to deliver and "
            "no process group to signal. Run it under Linux (a container is enough -- this "
            "module imports only the standard library)."
        )
        return 2

    print(f"python {sys.version.split()[0]} on {sys.platform}, pid {os.getpid()}")
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        check_session_gives_the_child_its_own_group(directory)
        check_killpg_sigterm_reaches_the_grandchild(directory)
        check_stop_drains_and_kill_does_not(directory)

    if _FAILURES:
        print(f"\n{len(_FAILURES)} FAILED: {', '.join(_FAILURES)}")
        return 1
    print("\nall four links proved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

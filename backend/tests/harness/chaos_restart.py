"""Kill a worker, bring it back, and find out what the durable plane really held.

ACC brief item 1. Scaffolding only: the primitives the durability scenarios
(14-18, 20, 23) will be written against, and nothing that asserts anything about
a feature. Those scenarios test code that does not exist yet, and a scenario
written ahead of its subject is a scenario written against a guess.

**The kill is abrupt, and that is the whole point.** `stop()` exists for
teardown; `kill()` is what a chaos scenario calls, and it sends the signal a
process cannot catch, cannot handle, and cannot drain in front of. A "kill" the
worker gets to shut down cleanly through proves the graceful path, which is not
the path that loses data. `SIGKILL` on POSIX, `TerminateProcess` (via
`taskkill /F /T`) on Windows -- and in both cases the whole tree, because a
worker script that has spawned a child leaves it holding a task queue after the
parent is gone, and the next scenario then runs against a worker nobody started.

**Nothing here opens a connection.** Clients and workflow handles arrive as
arguments; `wait_for_workflow` takes anything with `describe()`. That is partly
a design preference and partly the suite boundary: modules under `tests/` are
AST-scanned by
`tests/platform/test_the_normal_suite_never_needs_live_infrastructure.py`, and a
helper that constructed a driver would have to be classified live as a whole
module -- dragging every scenario that imported it into the live suite whether
or not it needed infrastructure. The live boundary belongs to the scenario.

**Waiting is polling, and a poll during a kill window fails.** A Temporal query
against a workflow whose only worker has just been killed does not return
"pending" -- it raises, because there is no poller to answer it. Every waiter
here therefore treats an exception from the probe as "not yet" and keeps going
until the deadline, then fails with the last error attached. A waiter that let
the first exception through would fail in the kill window every single time and
be read, correctly, as a flaky harness.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "GRACE_SECONDS",
    "ORDER_DISCOVERY_WORKER",
    "RETURN_WORKFLOW_WORKER",
    "ChaosTimeout",
    "DescribableWorkflow",
    "WorkerProcess",
    "WorkerSpec",
    "assert_once",
    "assert_remains_once",
    "wait_for_workflow",
    "wait_until",
]

#: Long enough for a cold worker to reconnect to Temporal and start polling,
#: short enough that a scenario which will never pass says so inside a suite run
#: rather than at a CI job timeout with no message.
DEFAULT_TIMEOUT_SECONDS = 60.0

#: Fast enough that a state transition is not credited to the wrong side of a
#: kill, slow enough not to spin a core while a worker boots.
DEFAULT_POLL_INTERVAL_SECONDS = 0.25

#: How long a polite teardown waits before it stops being polite. Teardown time
#: is paid on every scenario and buys nothing, so this is short: a worker that
#: has not drained in three seconds is going to be forced anyway.
GRACE_SECONDS = 3.0

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


class ChaosTimeout(AssertionError):
    """A waiter gave up.

    An `AssertionError` on purpose: a durability scenario that never reaches its
    state has failed, and reporting that as an infrastructure exception invites
    it to be retried rather than read.
    """


class DescribableWorkflow(Protocol):
    """The part of a Temporal workflow handle the waiters need.

    Structural rather than `temporalio.client.WorkflowHandle`, so the waiters
    can be exercised without a Temporal server -- and so this module never
    imports a client it might be tempted to construct.
    """

    async def describe(self) -> Any: ...


# ---------------------------------------------------------------------------
# Processes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorkerSpec:
    """How to launch one worker, exactly as the deployment launches it.

    `argv` is deliberately the real entry point rather than an in-process
    `Worker(...)`. A worker killed in-process loses nothing an OS-level kill
    loses: the interpreter is still there, `finally` blocks still run, and
    buffered writes still flush. Only a separate process can be taken away
    mid-transaction, which is the failure these scenarios are about.
    """

    name: str
    argv: Sequence[str]
    #: Defaults to `backend/`, which is what `compose.yaml` and
    #: `scripts/run_worker_host.sh` both run these scripts from.
    cwd: Path = _BACKEND_ROOT
    #: Overlaid on `os.environ`, never replacing it: the worker needs the
    #: datastore addresses the suite is already configured with.
    env: dict[str, str] = field(default_factory=dict)


def _script(name: str) -> tuple[str, ...]:
    return (sys.executable, str(_BACKEND_ROOT / "scripts" / name))


#: The worker that owns `ReturnCaseWorkflow` -- the one every durability
#: scenario in items 14-18 kills.
RETURN_WORKFLOW_WORKER = WorkerSpec(
    name="return-workflow-worker",
    argv=_script("run_return_workflow_worker.py"),
)

ORDER_DISCOVERY_WORKER = WorkerSpec(
    name="order-discovery-worker",
    argv=_script("run_order_discovery_worker.py"),
)


class WorkerProcess:
    """One worker, startable, killable, and startable again.

    Idempotent in both directions by design (contracts.md sect. 3): `start()` on
    a running process is a no-op and `kill()` on a dead one is a no-op, so a
    scenario that fails halfway leaves teardown able to run unconditionally
    without a second error burying the first.
    """

    def __init__(self, spec: WorkerSpec) -> None:
        self._spec = spec
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def name(self) -> str:
        return self._spec.name

    @property
    def pid(self) -> int | None:
        return None if self._process is None else self._process.pid

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        """Launch the worker, unless it is already up."""
        if self.is_running:
            return

        environment = {**os.environ, **self._spec.env}
        # A new process group so the whole tree can be signalled at once. Set at
        # launch because it cannot be arranged afterwards, and a worker whose
        # children outlive it holds a task queue nobody can find.
        extra: dict[str, Any] = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
            if os.name == "nt"
            else {"start_new_session": True}
        )
        self._process = subprocess.Popen(  # noqa: S603 -- argv is built here, never from input
            list(self._spec.argv),
            cwd=str(self._spec.cwd),
            env=environment,
            **extra,
        )

    def _signal_tree(self, pid: int, *, force: bool) -> None:
        """Signal the worker and everything descended from it.

        `force` is the difference between "please stop" and "you are gone":
        `SIGTERM`/`SIGKILL` to the process group on POSIX, `taskkill /T` with or
        without `/F` on Windows.

        **The tree is walked from the parent, so this must be called while the
        parent is alive.** That is a real limitation and it is why both `kill()`
        and `stop()` reap before letting the parent go rather than after. A job
        object would have removed the constraint, except that the worker spawns
        its own children in the microseconds between `CreateProcess` returning
        and an assignment landing -- measured, not assumed: the grandchild came
        back in no job at all -- so the job would have silently reaped less than
        this does.

        Never raises. A signal that finds nothing to signal has succeeded, and a
        teardown error would bury the assertion that explains the failure.
        """
        if os.name == "nt":  # pragma: no cover - exercised only on Windows
            command = ["taskkill", "/T", "/PID", str(pid)]
            if force:
                command.insert(1, "/F")
            subprocess.run(command, capture_output=True, check=False)  # noqa: S603, S607
            return
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL if force else signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            with suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)

    def kill(self, *, timeout: float = 10.0) -> None:
        """Take the worker away without warning, children included.

        No `terminate()` first. A scenario that asked for a kill and got a
        graceful shutdown proves the drain path works and says nothing about
        what survives an unplanned loss, which is the only question these
        scenarios ask.
        """
        process = self._process
        if process is None:
            return

        self._signal_tree(process.pid, force=True)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:  # pragma: no cover - unkillable process
            raise ChaosTimeout(
                f"{self.name} (pid {process.pid}) survived an unconditional kill for "
                f"{timeout}s -- the scenario below it cannot mean anything, because the "
                "worker it believes is gone is still polling the task queue"
            ) from error
        finally:
            self._process = None

    def stop(self, *, timeout: float = 15.0, grace: float = GRACE_SECONDS) -> None:
        """Shut the worker down as politely as the platform allows. **Teardown only.**

        Named apart from `kill()` so the two can never be confused at a call
        site: a scenario that used this would be testing the drain path while
        claiming to test crash recovery, and would pass.

        Polite to the whole tree, not just the parent, and forceful afterwards.
        A worker that shut down cleanly while leaving a child polling the task
        queue hands the next scenario exactly the poisoned starting state an
        ungraceful exit would have -- so teardown reaps either way, and reaps
        *before* the parent is gone, while the tree can still be walked.

        **On Windows the polite step is skipped, because there is no polite
        step.** `taskkill` without `/F` posts `WM_CLOSE`, which a console
        process has no message loop to receive, and `Popen.terminate()` is
        `TerminateProcess` -- already un-catchable. Trying anyway cost a full
        grace period per teardown and terminated nothing; the honest version
        goes straight to the forceful path and says so here.
        """
        process = self._process
        if process is None:
            return

        if os.name != "nt" and process.poll() is None:
            self._signal_tree(process.pid, force=False)
            deadline = time.monotonic() + grace
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)

        self._signal_tree(process.pid, force=True)
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=timeout)
        self._process = None

    def restart(self) -> None:
        """Kill and start again -- the shape of every scenario in items 14-18."""
        self.kill()
        self.start()

    def __enter__(self) -> WorkerProcess:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# Waiting
# ---------------------------------------------------------------------------


async def wait_until[T](
    probe: Callable[[], Awaitable[T]],
    predicate: Callable[[T], bool],
    *,
    what: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> T:
    """Poll `probe` until `predicate` holds, tolerating probes that raise.

    A probe raising is "not yet", not a failure. Across a kill window it is the
    *expected* answer: a query has no worker to answer it, an HTTP read reaches
    a process that is not there. Treating the first exception as fatal would
    make every restart scenario fail in the gap it exists to open.

    `what` completes the sentence "timed out waiting for …" and is the only
    thing a reader gets at 3am, so it is required rather than defaulted.
    """
    deadline = time.monotonic() + timeout_seconds
    last_seen: T | None = None
    last_error: BaseException | None = None

    while True:
        try:
            observed = await probe()
        except Exception as error:  # noqa: BLE001 -- an unavailable probe is "not yet"
            last_error = error
        else:
            last_error = None
            last_seen = observed
            if predicate(observed):
                return observed

        if time.monotonic() >= deadline:
            detail = (
                f"the probe never succeeded; last error: {last_error!r}"
                if last_error is not None
                else f"last observed: {last_seen!r}"
            )
            raise ChaosTimeout(f"timed out after {timeout_seconds}s waiting for {what} -- {detail}")

        await asyncio.sleep(interval)


async def wait_for_workflow(
    handle: DescribableWorkflow,
    predicate: Callable[[Any], bool],
    *,
    what: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> Any:
    """Wait for a workflow's description to satisfy `predicate`.

    `describe()` rather than a query, because `describe` is answered by the
    Temporal service from history and a query is answered by a worker. During
    the window a chaos scenario has deliberately opened, there is no worker --
    so a waiter built on queries cannot observe the workflow at all in exactly
    the interval the scenario cares about.

    The predicate takes the whole description rather than a status, so a caller
    can wait on `status`, on `close_time`, or on whatever the scenario actually
    means, without this signature growing a keyword per question.
    """
    return await wait_until(
        handle.describe,
        predicate,
        what=what,
        timeout_seconds=timeout_seconds,
        interval=interval,
    )


# ---------------------------------------------------------------------------
# Once, and still once
# ---------------------------------------------------------------------------


def assert_once[T](records: Iterable[T], *, key: Callable[[T], object], what: str) -> T:
    """Exactly one record, by `key`. Returns it, so the scenario can go on.

    The delivery guarantee in contracts.md sect. 7 is *effectively once*, and
    the observable it names is "exactly one message on B". Both failure
    directions are real and they mean opposite things: none means the send was
    lost, several mean receiver dedupe did not hold. So the message says which,
    and prints what was actually there -- an assertion that only says `1 != 2`
    sends the reader back to the database to find out what the second one was.
    """
    seen = list(records)
    distinct = {key(record) for record in seen}

    if len(distinct) == 1 and len(seen) == 1:
        return seen[0]

    if not seen:
        raise AssertionError(
            f"expected exactly one {what} and found none -- the send was lost, not deduplicated"
        )
    raise AssertionError(
        f"expected exactly one {what} and found {len(seen)} "
        f"({len(distinct)} distinct by key): {seen!r}. More than one means receiver "
        "dedupe did not hold on the delivery identity."
    )


async def assert_remains_once[T](
    fetch: Callable[[], Awaitable[Iterable[T]]],
    *,
    key: Callable[[T], object],
    what: str,
    for_seconds: float = 5.0,
    interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> T:
    """Exactly one, and still exactly one after the retries have had their chance.

    The check `assert_once` performs is a snapshot, and a snapshot taken
    immediately after a restart is taken *before* the duplicate arrives: the
    delivery being retried at-least-once is precisely why receiver dedupe exists,
    so the interesting window opens after the first observation, not at it.
    Holding the assertion open across that window is the difference between
    testing the guarantee and testing the timing.

    Fails on the first violation rather than at the end, so the failure names the
    moment the second delivery landed.
    """
    deadline = time.monotonic() + for_seconds
    result = assert_once(await fetch(), key=key, what=what)

    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        result = assert_once(await fetch(), key=key, what=what)

    return result

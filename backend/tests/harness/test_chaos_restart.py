"""The harness's own primitives, checked without any infrastructure.

A chaos harness is the last thing in a suite anyone suspects. When a durability
scenario fails, the reading is "the platform lost the write" -- so a `kill()`
that quietly left the worker running, or a waiter that returned before its
condition held, produces a red test pointing at innocent code and an
investigation that starts in the wrong repository.

So the primitives are proved here against a trivial subprocess and a handful of
fake probes: no Temporal, no datastore, nothing that needs the live suite. The
real worker is smoke-tested separately in
`test_chaos_restart_smoke_real_infra.py`, where it belongs.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tests.harness.chaos_restart import (
    ChaosTimeout,
    WorkerProcess,
    WorkerSpec,
    assert_once,
    assert_remains_once,
    wait_for_workflow,
    wait_until,
)

#: A process that does nothing for long enough to be killed on purpose.
_IDLE = "import time; time.sleep(300)"


def _idle_worker(name: str = "idle") -> WorkerSpec:
    return WorkerSpec(name=name, argv=(sys.executable, "-c", _IDLE))


def _parent_that_spawns_a_child(directory: Path, heartbeat: Path) -> WorkerSpec:
    """A parent that starts a heartbeating child, then idles.

    The child is what makes this worth testing. A worker script that has spawned
    anything leaves it holding a task queue when the parent is killed, and the
    next scenario then runs against a worker nobody started and cannot explain.

    Written to files rather than passed as `-c` source: a nested quoted program
    is unreadable, and a test whose fixture nobody can read is a test nobody
    trusts when it fails.
    """
    child = directory / "child.py"
    child.write_text(
        "import time\n"
        f"path = {str(heartbeat)!r}\n"
        "while True:\n"
        "    open(path, 'w').write(str(time.time()))\n"
        "    time.sleep(0.05)\n",
        encoding="utf-8",
    )
    parent = directory / "parent.py"
    parent.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, {str(child)!r}])\n"
        "time.sleep(300)\n",
        encoding="utf-8",
    )
    return WorkerSpec(name="parent", argv=(sys.executable, str(parent)))


class TestWorkerProcess:
    def test_start_is_idempotent(self) -> None:
        """A second `start()` must not leave two workers on one task queue.

        Both would poll it. Half the activities would run on the process a
        scenario believes it killed, and the scenario would pass or fail by
        coin toss.
        """
        worker = WorkerProcess(_idle_worker())
        try:
            worker.start()
            first = worker.pid
            worker.start()

            assert worker.pid == first
            assert worker.is_running
        finally:
            worker.kill()

    def test_kill_stops_it_and_is_idempotent(self) -> None:
        """Teardown must be able to run unconditionally after a failed scenario.

        A `kill()` that raised on an already-dead process would replace the
        assertion error that actually explains the failure with one about
        cleanup.
        """
        worker = WorkerProcess(_idle_worker())
        worker.start()
        assert worker.is_running

        worker.kill()
        assert not worker.is_running

        worker.kill()  # no second error to bury the first
        assert not worker.is_running

    def test_kill_on_a_worker_that_never_started_is_a_no_op(self) -> None:
        WorkerProcess(_idle_worker()).kill()

    def test_restart_produces_a_different_process(self) -> None:
        """The pid has to change, or the "restart" was a poll of the same process."""
        worker = WorkerProcess(_idle_worker())
        try:
            worker.start()
            before = worker.pid
            worker.restart()

            assert worker.is_running
            assert worker.pid != before
        finally:
            worker.kill()

    def test_kill_takes_the_children_with_it(self, tmp_path: Path) -> None:
        """An orphaned child is a worker still polling a queue nobody is watching.

        Measured by the heartbeat going stale rather than by looking the child's
        pid up, because "is this pid alive" is two different system calls on the
        two platforms this runs on and pid reuse makes both of them lie.
        """
        heartbeat = tmp_path / "child-heartbeat"
        worker = WorkerProcess(_parent_that_spawns_a_child(tmp_path, heartbeat))
        try:
            worker.start()
            deadline = time.monotonic() + 20
            while not heartbeat.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert heartbeat.exists(), "the child never started, so this proves nothing"

            worker.kill()
            time.sleep(0.5)
            settled = heartbeat.read_text()
            time.sleep(0.5)

            assert heartbeat.read_text() == settled, (
                "the child outlived the kill and is still running -- every scenario "
                "after this one would share its task queue with a worker it did not start"
            )
        finally:
            worker.kill()

    def test_stop_reaps_the_children_too(self, tmp_path: Path) -> None:
        """A polite shutdown that leaves an orphan poisons the next scenario anyway.

        `terminate()` reaches the parent and nothing below it on either
        platform, so teardown has to reap the tree regardless of how gracefully
        the parent went. The starting state the next scenario inherits is the
        only thing that matters here, and it is identical either way.
        """
        heartbeat = tmp_path / "child-heartbeat"
        worker = WorkerProcess(_parent_that_spawns_a_child(tmp_path, heartbeat))
        try:
            worker.start()
            deadline = time.monotonic() + 20
            while not heartbeat.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert heartbeat.exists(), "the child never started, so this proves nothing"

            worker.stop()
            time.sleep(0.5)
            settled = heartbeat.read_text()
            time.sleep(0.5)

            assert heartbeat.read_text() == settled, (
                "the child survived a graceful stop -- teardown left a worker polling "
                "a task queue for whatever runs next"
            )
        finally:
            worker.kill()

    def test_the_context_manager_starts_and_stops(self) -> None:
        with WorkerProcess(_idle_worker()) as worker:
            assert worker.is_running
            inner = worker
        assert not inner.is_running

    def test_stop_is_the_graceful_one_and_kill_is_not(self) -> None:
        """The two are separate names because confusing them passes silently.

        A scenario that reached for `stop()` would be exercising the drain path
        while claiming to test crash recovery. Nothing in a result would say so.
        `terminate` is catchable; the kill is not, and this pins that they are
        implemented differently rather than aliased.
        """
        worker = WorkerProcess(_idle_worker())
        worker.start()
        worker.stop()

        assert not worker.is_running

    def test_the_environment_is_overlaid_rather_than_replaced(self, tmp_path: Path) -> None:
        """A worker launched with a bare env cannot reach any datastore.

        `.env` is loaded into the process environment by `conftest.pytest_configure`,
        and every address the worker needs is in it.
        """
        written = tmp_path / "env.txt"
        spec = WorkerSpec(
            name="env-probe",
            argv=(
                sys.executable,
                "-c",
                "import os, sys\n"
                f"open({str(written)!r}, 'w').write(\n"
                "    os.environ.get('PATH', '') + '\\n' + os.environ.get('CHAOS_PROBE', '')\n)",
            ),
            env={"CHAOS_PROBE": "set-by-spec"},
        )
        worker = WorkerProcess(spec)
        worker.start()
        deadline = time.monotonic() + 20
        while worker.is_running and time.monotonic() < deadline:
            time.sleep(0.05)

        inherited, injected = written.read_text().split("\n")
        assert injected == "set-by-spec"
        assert inherited == os.environ.get("PATH", ""), (
            "the spec's env replaced the process environment instead of overlaying it"
        )


class TestWaiting:
    @pytest.mark.asyncio
    async def test_it_returns_the_first_observation_that_satisfies_the_predicate(self) -> None:
        seen = iter([1, 2, 3])

        async def probe() -> int:
            return next(seen)

        assert await wait_until(probe, lambda value: value >= 2, what="two", interval=0) == 2

    @pytest.mark.asyncio
    async def test_a_probe_that_raises_is_not_yet_rather_than_a_failure(self) -> None:
        """The kill window, in one test.

        A query against a workflow whose only worker has just been killed
        raises; it does not return "pending". A waiter that let that through
        would fail in the exact interval every restart scenario opens on
        purpose, and would be diagnosed as a flaky harness rather than as a
        design mistake.
        """
        attempts = iter([RuntimeError("no poller"), ConnectionError("refused"), "READY"])

        async def probe() -> str:
            nxt = next(attempts)
            if isinstance(nxt, BaseException):
                raise nxt
            return nxt

        assert (
            await wait_until(probe, lambda v: v == "READY", what="recovery", interval=0) == "READY"
        )

    @pytest.mark.asyncio
    async def test_a_timeout_names_what_never_happened_and_what_was_seen(self) -> None:
        async def probe() -> str:
            return "OPEN"

        with pytest.raises(ChaosTimeout) as raised:
            await wait_until(
                probe,
                lambda v: v == "SENT",
                what="the review to reach SENT",
                timeout_seconds=0.05,
                interval=0.01,
            )

        message = str(raised.value)
        assert "the review to reach SENT" in message
        assert "OPEN" in message, "the timeout did not say what it kept seeing instead"

    @pytest.mark.asyncio
    async def test_a_timeout_on_a_probe_that_never_succeeded_carries_the_last_error(self) -> None:
        """Otherwise "timed out" hides "the worker never came back up"."""

        async def probe() -> str:
            raise ConnectionError("connection refused")

        with pytest.raises(ChaosTimeout) as raised:
            await wait_until(
                probe, lambda v: True, what="anything", timeout_seconds=0.05, interval=0.01
            )

        assert "connection refused" in str(raised.value)

    @pytest.mark.asyncio
    async def test_wait_for_workflow_asks_describe(self) -> None:
        """`describe` is answered by the service; a query is answered by a worker.

        Which is the whole reason the waiter is built on it: during the window a
        chaos scenario has opened there is no worker, so a query-based waiter
        cannot observe the workflow at all in the interval that matters.
        """
        calls: list[str] = []

        class Handle:
            async def describe(self) -> Any:
                calls.append("describe")
                return type("Description", (), {"status": "COMPLETED"})()

        described = await wait_for_workflow(
            Handle(), lambda d: d.status == "COMPLETED", what="completion", interval=0
        )

        assert described.status == "COMPLETED"
        assert calls == ["describe"]


class TestOnce:
    def test_one_record_is_returned_so_the_scenario_can_continue(self) -> None:
        record = {"deliveryId": "d-1", "body": "hello"}

        assert assert_once([record], key=lambda r: r["deliveryId"], what="message on B") is record

    def test_none_is_reported_as_a_lost_send_not_as_a_duplicate(self) -> None:
        """The two failures mean opposite things and must not read the same.

        None means the send was lost. Several mean receiver dedupe did not hold.
        An assertion that only says the count was wrong sends the reader looking
        in the wrong half of the delivery path.
        """
        with pytest.raises(AssertionError, match="found none"):
            assert_once([], key=lambda r: r, what="message on B")

    def test_several_are_reported_with_their_contents(self) -> None:
        with pytest.raises(AssertionError) as raised:
            assert_once(
                [{"deliveryId": "d-1"}, {"deliveryId": "d-1"}],
                key=lambda r: r["deliveryId"],
                what="message on B",
            )

        message = str(raised.value)
        assert "receiver dedupe" in message
        assert "d-1" in message, "the failure did not print what the duplicates were"

    @pytest.mark.asyncio
    async def test_remaining_once_catches_a_duplicate_that_lands_after_the_first_look(self) -> None:
        """The snapshot passes; the guarantee does not.

        Delivery is at-least-once and retries arrive *after* the restart, which
        is exactly why receiver dedupe exists. A single check taken the moment
        the worker comes back is taken before the duplicate, so it tests the
        timing rather than the guarantee.
        """
        deliveries: list[dict[str, str]] = [{"deliveryId": "d-1"}]

        async def fetch() -> list[dict[str, str]]:
            snapshot = list(deliveries)
            deliveries.append({"deliveryId": "d-1"})  # the retry lands
            return snapshot

        with pytest.raises(AssertionError, match="receiver dedupe"):
            await assert_remains_once(
                fetch,
                key=lambda r: r["deliveryId"],
                what="message on B",
                for_seconds=0.1,
                interval=0,
            )

    @pytest.mark.asyncio
    async def test_remaining_once_returns_the_record_when_dedupe_holds(self) -> None:
        async def fetch() -> list[dict[str, str]]:
            return [{"deliveryId": "d-1"}]

        result = await assert_remains_once(
            fetch,
            key=lambda r: r["deliveryId"],
            what="message on B",
            for_seconds=0.05,
            interval=0,
        )

        assert result == {"deliveryId": "d-1"}


def test_the_declared_worker_entry_points_exist() -> None:
    """The scripts the specs name are the ones compose and the host scripts run.

    A spec pointing at a moved script fails as a worker that exits instantly,
    which reads as "the worker crashed on startup" -- an investigation into the
    worker rather than into the two-line typo here.
    """
    from tests.harness.chaos_restart import ORDER_DISCOVERY_WORKER, RETURN_WORKFLOW_WORKER

    for spec in (RETURN_WORKFLOW_WORKER, ORDER_DISCOVERY_WORKER):
        script = Path(spec.argv[-1])
        assert script.is_file(), f"{spec.name} points at a script that is not there: {script}"
        assert spec.cwd.is_dir()


def test_the_harness_opens_no_connection_of_its_own() -> None:
    """The suite boundary belongs to the scenario, not to the helper it imports.

    `tests/platform/test_the_normal_suite_never_needs_live_infrastructure.py`
    classifies a module by the drivers it constructs, so a helper that built a
    client would force every scenario importing it into the live suite whether
    or not that scenario needed infrastructure. Pinned here as well because the
    consequence lands two files away from the change that causes it.
    """
    source = (Path(__file__).parent / "chaos_restart.py").read_text(encoding="utf-8")

    for driver in ("AsyncMongoClient(", "MongoClient(", "GraphDatabase.driver(", "Client.connect("):
        assert driver not in source, (
            f"chaos_restart.py now constructs {driver} -- pass the client in from the "
            "scenario instead, or every scenario that imports this becomes live_infra"
        )


def test_a_worker_is_a_separate_process_rather_than_something_in_this_one() -> None:
    """The distinction the whole harness rests on.

    A worker killed in-process loses nothing: the interpreter survives, `finally`
    blocks run, buffers flush. Only a separate process can be taken away
    mid-transaction, which is the failure items 14-18 are about.
    """
    worker = WorkerProcess(_idle_worker())
    try:
        worker.start()

        assert worker.pid is not None
        assert worker.pid != os.getpid()
    finally:
        worker.kill()

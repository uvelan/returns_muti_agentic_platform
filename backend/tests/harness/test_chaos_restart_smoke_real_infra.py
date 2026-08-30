"""The harness against the real worker, once (ACC brief, item 1).

`test_chaos_restart.py` proves the primitives against a trivial subprocess and
runs in the normal suite. What it cannot prove is that the *deployment's* worker
starts from this harness at all: the script paths in the specs, the working
directory, and the inherited environment are three assumptions that only fail
where the real process is launched.

So this file launches `run_return_workflow_worker.py` exactly as `compose.yaml`
and `scripts/run_worker_host.sh` do, kills it, and starts it again. It is a
smoke test of the harness and asserts nothing about return behaviour -- the
durability scenarios (items 14-18, 20, 23) are written after V3 merges, against
code that does not exist yet.

**Live by filename.** `tests/conftest.py::_suite_of` reads the `_real_infra.py`
suffix, so this is classified `live_infra` and deselected from the default run
by `addopts`. The marker is declared as well: the suffix and the marker are two
independent statements of the same fact and the explicit one wins in
`_SUITE_MARKERS`, which matters if this file is ever renamed.

It needs the stack because the worker does -- Mongo, Neo4j, SQL Server, Valkey
and Temporal are all opened during `run_return_workflow_worker._run` before it
ever reaches the task queue. `scripts/dev/run_real_infra_suite.sh` preflights
all five, so a stopped stack reads as "start the stack" rather than as this
test failing.
"""

from __future__ import annotations

import time

import pytest

from tests.harness.chaos_restart import RETURN_WORKFLOW_WORKER, WorkerProcess

pytestmark = pytest.mark.live_infra

#: How long the worker gets to prove it did not die during startup.
#:
#: The failure this catches is a worker that launches, raises on a connection or
#: a configuration read, and exits -- which `is_running` reports correctly one
#: millisecond after `start()` and incorrectly a second later. Long enough to
#: cover `resolve_process_configuration` plus five driver constructions.
_SETTLE_SECONDS = 20.0


def _running_after_settling(worker: WorkerProcess) -> bool:
    """Whether the worker is still up once it has had time to fall over."""
    deadline = time.monotonic() + _SETTLE_SECONDS
    while time.monotonic() < deadline:
        if not worker.is_running:
            return False
        time.sleep(0.5)
    return worker.is_running


def test_the_real_return_workflow_worker_starts_dies_and_comes_back() -> None:
    """Start, kill, restart -- the shape every durability scenario is built on.

    Deliberately three assertions and no fourth. Whether the workflow survived
    is not a question this file can answer yet, and a smoke test that reached
    for one would be a durability scenario written against unbuilt code.
    """
    worker = WorkerProcess(RETURN_WORKFLOW_WORKER)
    try:
        worker.start()
        first = worker.pid
        assert _running_after_settling(worker), (
            f"{worker.name} exited during startup -- the harness launches it the way "
            "compose.yaml does, so this is the stack or the configuration, not the kill"
        )

        worker.kill()
        assert not worker.is_running

        worker.start()
        assert _running_after_settling(worker), (
            f"{worker.name} did not come back after a kill -- a restart that cannot "
            "happen makes every scenario in items 14-18 unwritable"
        )
        assert worker.pid != first
    finally:
        worker.stop()

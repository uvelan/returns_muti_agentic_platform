"""Every worker process beats, and one place decides which must.

The audit recorded a contradiction it could not explain: `worker_heartbeats` held
**four** documents while five workers reported `1/1 live` through
`/api/config/adoption`. Two liveness sources disagreeing, with no cause given.

The cause was that they had different writers. Adoption was reported by a shared
helper every worker calls; the heartbeat was a loop each worker hand-rolled in
its own entrypoint. `integration-outbox-worker` got the shared one and never got
the bespoke one -- it declares its process class and reports adoption, and the
string "heartbeat" appeared nowhere in its module.

Three further lists disagreed about which workers matter. `/health/ready`
checked three classes, the hardening audit checked the same three and only
WARNed on a miss, and `REQUIRED_PROCESS_CLASSES` named six. So
`order-discovery-worker`, `integration-outbox-worker` and `housekeeping-worker`
could all be dead with every surface reporting green.

These tests hold both halves shut: the heartbeat is started where adoption is,
and the set of classes that must beat has one definition.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from return_platform.configuration.process_adoption import (
    HEARTBEAT_PROCESS_CLASSES,
    REQUIRED_PROCESS_CLASSES,
)

_BACKEND = Path(__file__).resolve().parents[1]
_SRC = _BACKEND / "src" / "return_platform"

#: Every worker entrypoint, and the process class it declares.
#:
#: Hand-kept, and asserted against the declared class in each module below --
#: so a new worker whose class is not here fails, rather than quietly joining
#: the set of processes nobody checks.
_WORKER_MODULES: dict[str, Path] = {
    "return-workflow-worker": _BACKEND / "scripts" / "run_return_workflow_worker.py",
    "order-discovery-worker": _BACKEND / "scripts" / "run_order_discovery_worker.py",
    "return-orchestrator": _BACKEND / "scripts" / "run_return_orchestrator.py",
    "outbox-publisher": _BACKEND / "scripts" / "run_outbox_publisher.py",
    "housekeeping-worker": _BACKEND / "scripts" / "run_housekeeping_worker.py",
    "integration-outbox-worker": _SRC / "workers" / "integration_outbox.py",
}


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


@pytest.mark.parametrize(("process_class", "path"), sorted(_WORKER_MODULES.items()))
def test_every_worker_starts_the_shared_activation(process_class: str, path: Path) -> None:
    """`activation.start()` is what begins both adoption and the heartbeat.

    A worker that builds an activation and never starts it adopts nothing and
    beats nothing, which is the state that is indistinguishable from a process
    that was never deployed.
    """
    assert path.is_file(), f"{process_class} entrypoint has moved; update this test with it"
    source = _source(path)

    assert "build_worker_runtime_activation" in source, (
        f"{process_class} does not build the shared runtime activation, so it "
        f"reports neither adoption nor liveness."
    )
    assert "activation.start()" in source, (
        f"{process_class} builds an activation and never starts it."
    )


@pytest.mark.parametrize(("process_class", "path"), sorted(_WORKER_MODULES.items()))
def test_no_worker_hand_rolls_its_own_heartbeat(process_class: str, path: Path) -> None:
    """One mechanism, so a new worker cannot forget to copy it.

    Every hand-rolled loop was removed when heartbeating moved into
    `WorkerRuntimeActivation.start()`. A worker that grows one again has
    reintroduced exactly the drift that lost `integration-outbox-worker`, and it
    would also double-write the document.
    """
    tree = ast.parse(_source(path))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "heartbeat"
    ]

    assert calls == [], (
        f"{process_class} calls `.heartbeat(...)` directly at line(s) "
        f"{[node.lineno for node in calls]}. Heartbeating belongs to "
        f"`WorkerRuntimeActivation.start()`, which every worker already calls."
    )


def test_the_heartbeat_set_covers_every_worker() -> None:
    """The list the health surfaces read is the list of workers that exist."""
    assert HEARTBEAT_PROCESS_CLASSES == frozenset(_WORKER_MODULES)


def test_the_heartbeat_set_is_not_the_release_adoption_set() -> None:
    """Two questions, two sets, and conflating them would break one of them.

    `REQUIRED_PROCESS_CLASSES` decides whether a *release* is live and includes
    the API while excluding housekeeping -- a reclaimer being down must not hold
    a release at ACTIVATING. `HEARTBEAT_PROCESS_CLASSES` decides whether a
    *process* is alive, and housekeeping being dead is precisely what went
    unnoticed for two days.
    """
    assert "api" in REQUIRED_PROCESS_CLASSES
    assert "api" not in HEARTBEAT_PROCESS_CLASSES
    assert "housekeeping-worker" in HEARTBEAT_PROCESS_CLASSES
    assert "housekeeping-worker" not in REQUIRED_PROCESS_CLASSES


def test_the_shared_activation_starts_a_heartbeat() -> None:
    """The whole fix, asserted at its source."""
    source = _source(_SRC / "configuration" / "runtime_activation.py")

    assert "run_worker_heartbeat" in source
    assert "heartbeat_writer" in source

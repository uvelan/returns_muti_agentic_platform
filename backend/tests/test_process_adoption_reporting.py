"""`ACTIVATED != LIVE` -- and the platform can tell the difference.

Promoting a release moves a pointer in the graph. It does not move the API
process, and it does not move the five workers. CFG-01 made them adopt; these
cover the half that makes adoption observable, so "the release is active" stops
being a claim about a pointer and becomes a statement about processes.

The distinction is the point, so it is tested at the boundary that decides it:
a release with three of five classes adopted must come back ACTIVATING and name
the two it is waiting on -- never LIVE, and never a bare boolean that leaves an
operator guessing which process is behind.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from return_platform.configuration.process_adoption import (
    API_PROCESS_CLASS,
    PROCESS_ADOPTIONS_COLLECTION,
    REQUIRED_PROCESS_CLASSES,
    MongoProcessAdoptionStore,
    ProcessAdoptionRecord,
    ReleaseAdoptionStatus,
    adoption_record_from_snapshot,
    evaluate_release_adoption,
)
from return_platform.configuration.runtime_activation import (
    ApplicationAdoptionState,
    ProcessRuntimeState,
    run_process_adoption_reporter,
)
from return_platform.configuration.settings import Settings
from return_platform.configuration.snapshot import PinnedConfigurationSnapshot

_BACKEND = Path(__file__).resolve().parents[1]
_SCRIPTS = _BACKEND / "scripts"

#: Every process class that must adopt, and where its entry point lives. Two of
#: them are not under `scripts/`, which is exactly how `integration-outbox-worker`
#: went unnoticed the first time -- it is a module, so it is not in the directory
#: anyone checks.
_PROCESS_ENTRY_POINTS: dict[str, Path] = {
    API_PROCESS_CLASS: _BACKEND / "src" / "return_platform" / "main.py",
    "return-workflow-worker": _SCRIPTS / "run_return_workflow_worker.py",
    "order-discovery-worker": _SCRIPTS / "run_order_discovery_worker.py",
    "return-orchestrator": _SCRIPTS / "run_return_orchestrator.py",
    "outbox-publisher": _SCRIPTS / "run_outbox_publisher.py",
    "integration-outbox-worker": (
        _BACKEND / "src" / "return_platform" / "workers" / "integration_outbox.py"
    ),
}


def _record(
    process_class: str,
    *,
    instance_id: str = "instance-1",
    release_id: str = "release-2",
    head_revision: int = 2,
    source: str = "NEO4J_CONFIGURATION_GRAPH",
) -> ProcessAdoptionRecord:
    now = datetime.now(UTC)
    return ProcessAdoptionRecord(
        process_class=process_class,
        instance_id=instance_id,
        release_id=release_id,
        head_revision=head_revision,
        adopted_at=now,
        reported_at=now,
        source=source,
    )


def _all_adopted() -> list[ProcessAdoptionRecord]:
    return [_record(process_class) for process_class in sorted(REQUIRED_PROCESS_CLASSES)]


def _state(records: list[ProcessAdoptionRecord], **kwargs: Any) -> Any:
    return evaluate_release_adoption(
        activated_release_id=kwargs.get("activated_release_id", "release-2"),
        activated_head_revision=kwargs.get("activated_head_revision", 2),
        records=records,
    )


# --------------------------------------------------------------------------- #
# ACTIVATED != LIVE
# --------------------------------------------------------------------------- #


def test_a_release_every_required_class_reports_is_live() -> None:
    state = _state(_all_adopted())

    assert state.status is ReleaseAdoptionStatus.LIVE
    assert state.pending_process_classes == ()


def test_a_release_three_of_five_classes_adopted_is_not_live() -> None:
    """The scenario the whole distinction exists for."""

    adopted = sorted(REQUIRED_PROCESS_CLASSES)[:3]
    lagging = sorted(REQUIRED_PROCESS_CLASSES)[3:]
    records = [_record(process_class) for process_class in adopted]

    state = _state(records)

    assert state.status is ReleaseAdoptionStatus.ACTIVATING
    # Named, not counted. "2 of 5" does not tell an operator which container to
    # look at.
    assert state.pending_process_classes == tuple(lagging)


def test_a_class_still_on_the_previous_release_is_not_adopted() -> None:
    """Behind is not the same as absent, and both block LIVE."""

    records = _all_adopted()
    records[0] = _record(records[0].process_class, release_id="release-1", head_revision=1)

    state = _state(records)

    assert state.status is ReleaseAdoptionStatus.ACTIVATING
    assert state.pending_process_classes == (records[0].process_class,)
    behind = next(
        item for item in state.process_classes if item.process_class == records[0].process_class
    )
    assert behind.live_instances == 1
    assert behind.adopted_instances == 0


def test_the_right_release_at_the_wrong_revision_is_not_adopted() -> None:
    """A release id can be re-pointed while the head moves underneath it."""

    records = _all_adopted()
    records[0] = _record(records[0].process_class, release_id="release-2", head_revision=1)

    state = _state(records)

    assert state.status is ReleaseAdoptionStatus.ACTIVATING
    assert records[0].process_class in state.pending_process_classes


def test_one_lagging_replica_keeps_its_class_from_counting_as_adopted() -> None:
    """Any-instance would report a class as adopted while half its work is old."""

    records = _all_adopted()
    records.append(
        _record(
            "order-discovery-worker",
            instance_id="instance-2",
            release_id="release-1",
            head_revision=1,
        )
    )

    state = _state(records)

    assert state.status is ReleaseAdoptionStatus.ACTIVATING
    assert state.pending_process_classes == ("order-discovery-worker",)
    lagging = next(
        item for item in state.process_classes if item.process_class == "order-discovery-worker"
    )
    assert (lagging.live_instances, lagging.adopted_instances) == (2, 1)


def test_a_class_with_nothing_running_is_not_adopted() -> None:
    """Silence is absence. A stopped process must stop counting."""

    records = [
        record for record in _all_adopted() if record.process_class != "return-workflow-worker"
    ]

    state = _state(records)

    assert state.status is ReleaseAdoptionStatus.ACTIVATING
    assert state.pending_process_classes == ("return-workflow-worker",)
    missing = next(
        item for item in state.process_classes if item.process_class == "return-workflow-worker"
    )
    assert missing.live_instances == 0
    assert missing.adopted is False


def test_a_baseline_process_does_not_count_as_having_adopted_a_graph_release() -> None:
    """A process that fell back to the packaged YAML has not adopted anything."""

    records = _all_adopted()
    records[0] = _record(
        records[0].process_class,
        release_id="version-controlled-baseline",
        head_revision=0,
        source="VERSION_CONTROLLED_BASELINE",
    )

    state = _state(records)

    assert state.status is ReleaseAdoptionStatus.ACTIVATING
    assert records[0].process_class in state.pending_process_classes


def test_no_activated_release_is_its_own_answer() -> None:
    """Not the same as "nothing adopted" -- there is nothing to adopt."""

    state = evaluate_release_adoption(
        activated_release_id=None,
        activated_head_revision=None,
        records=_all_adopted(),
    )

    assert state.status is ReleaseAdoptionStatus.NO_ACTIVE_RELEASE
    assert state.pending_process_classes == tuple(sorted(REQUIRED_PROCESS_CLASSES))


def test_an_unexpected_process_class_is_reported_but_does_not_gate() -> None:
    """A process nobody declared required should be visible, not fatal."""

    records = [*_all_adopted(), _record("some-future-worker")]

    state = _state(records)

    assert state.status is ReleaseAdoptionStatus.LIVE
    extra = next(
        item for item in state.process_classes if item.process_class == "some-future-worker"
    )
    assert extra.required is False
    assert extra.adopted is True


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


class _FakeCollection:
    """Enough of a Motor collection for the store, with TTL honoured on read."""

    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.indexes: list[tuple[Any, ...]] = []

    async def create_index(self, keys: Any, **options: Any) -> None:
        self.indexes.append((keys, tuple(sorted(options.items()))))

    async def update_one(
        self, filter_: dict[str, Any], update: dict[str, Any], upsert: bool = False
    ) -> None:
        key = str(filter_["_id"])
        document = self.documents.setdefault(key, {"_id": key})
        document.update(update["$set"])

    def find(self, query: dict[str, Any]) -> Any:
        threshold = query["expiresAt"]["$gt"]
        matching = [
            document for document in self.documents.values() if document["expiresAt"] > threshold
        ]

        async def iterator() -> Any:
            for document in matching:
                yield document

        return iterator()


def _snapshot(release_id: str, head_revision: int) -> PinnedConfigurationSnapshot:
    return PinnedConfigurationSnapshot.model_construct(
        release_id=release_id,
        head_revision=head_revision,
        checksum_sha256="0" * 64,
        loaded_at=datetime.now(UTC),
        source="NEO4J_CONFIGURATION_GRAPH",
        # `model_construct` skips validation deliberately: a report is derived
        # from four scalar fields, and building a full 600-line configuration to
        # assert on a release id would test the fixture rather than the report.
        configuration=cast(Any, None),
        domain_payloads={},
    )


@pytest.mark.asyncio
async def test_a_report_expires_so_a_stopped_process_stops_counting() -> None:
    collection = _FakeCollection()
    store = MongoProcessAdoptionStore(collection)
    await store.ensure_indexes()
    assert any("expiresAt" in str(index[0]) for index in collection.indexes), (
        "without a TTL index a stopped process would keep a release LIVE forever"
    )

    await store.report(_record("order-discovery-worker"), ttl_seconds=5)
    assert len(await store.list_live()) == 1

    # Mongo's TTL monitor runs about once a minute, so a document outlives its
    # own expiry briefly. The read has to filter as well or an operator is told
    # a dead process is serving the release.
    stored = next(iter(collection.documents.values()))
    stored["expiresAt"] = datetime.now(UTC) - timedelta(seconds=1)
    assert await store.list_live() == ()


@pytest.mark.asyncio
async def test_a_report_is_derived_from_the_snapshot_the_process_is_serving() -> None:
    """No second place for a process to say what it runs."""

    snapshot = _snapshot("release-7", 7)
    record = adoption_record_from_snapshot(
        process_class="order-discovery-worker",
        instance_id="instance-1",
        snapshot=snapshot,
    )

    assert (record.release_id, record.head_revision) == ("release-7", 7)
    assert record.adopted_at == snapshot.loaded_at
    assert record.source == "NEO4J_CONFIGURATION_GRAPH"
    assert record.key == "order-discovery-worker:instance-1"


@pytest.mark.asyncio
async def test_the_reporter_follows_the_process_onto_a_new_release(
    test_settings: Settings,
) -> None:
    """Reporting tracks activation rather than latching at startup."""

    collection = _FakeCollection()
    store = MongoProcessAdoptionStore(collection)
    state = ProcessRuntimeState(
        process_class="order-discovery-worker",
        instance_id="instance-1",
        settings=test_settings,
        return_configuration=None,  # type: ignore[arg-type]
        return_configuration_snapshot=_snapshot("release-1", 1),
    )
    task = asyncio.create_task(run_process_adoption_reporter(state, store, interval_seconds=0))
    try:
        for _ in range(50):
            await asyncio.sleep(0)
            live = await store.list_live()
            if live and live[0].release_id == "release-1":
                break
        else:  # pragma: no cover - the reporter never reported
            pytest.fail("the reporter never reported the starting release")

        # The activator swaps the snapshot; the reporter must follow it.
        state.return_configuration_snapshot = _snapshot("release-2", 2)
        for _ in range(50):
            await asyncio.sleep(0)
            live = await store.list_live()
            if live and live[0].release_id == "release-2":
                break
        else:  # pragma: no cover
            pytest.fail("the reporter kept reporting the release it started on")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert len(collection.documents) == 1, "one instance must not accumulate documents"


@pytest.mark.asyncio
async def test_a_failed_report_does_not_take_the_process_down() -> None:
    """Being briefly unable to say what you run is not a reason to stop running."""

    class _BrokenStore:
        calls = 0

        async def report(self, record: ProcessAdoptionRecord, *, ttl_seconds: int) -> None:
            type(self).calls += 1
            raise RuntimeError("mongo is unreachable")

        async def list_live(self) -> tuple[ProcessAdoptionRecord, ...]:
            return ()

    state = ApplicationAdoptionState(
        process_class=API_PROCESS_CLASS,
        instance_id="instance-1",
        app_state=type("S", (), {"return_configuration_snapshot": _snapshot("release-1", 1)})(),
    )
    broken = _BrokenStore()
    task = asyncio.create_task(run_process_adoption_reporter(state, broken, interval_seconds=0))
    for _ in range(50):
        await asyncio.sleep(0)

    assert not task.done()
    assert _BrokenStore.calls > 1
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_the_api_reports_through_its_live_app_state() -> None:
    """A copied snapshot would report the release the API had at boot."""

    app_state = type("S", (), {"return_configuration_snapshot": _snapshot("release-1", 1)})()
    state = ApplicationAdoptionState(
        process_class=API_PROCESS_CLASS, instance_id="instance-1", app_state=app_state
    )
    assert state.return_configuration_snapshot is not None
    assert state.return_configuration_snapshot.release_id == "release-1"

    app_state.return_configuration_snapshot = _snapshot("release-2", 2)
    assert state.return_configuration_snapshot is not None
    assert state.return_configuration_snapshot.release_id == "release-2"


# --------------------------------------------------------------------------- #
# Production wiring
# --------------------------------------------------------------------------- #


def _called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def test_every_required_process_class_has_an_entry_point_that_reports() -> None:
    """C5 is a property of the process class, not of the ones we remembered.

    A required class whose entry point never reports would sit in
    `pending_process_classes` forever and no release would ever read LIVE --
    which looks like a broken gate rather than a missing reporter.
    """

    assert set(_PROCESS_ENTRY_POINTS) == set(REQUIRED_PROCESS_CLASSES)

    for process_class, path in _PROCESS_ENTRY_POINTS.items():
        if not path.exists():  # pragma: no cover - real-infra runs copy a subset
            pytest.skip(f"{path.name} is not in this run's copy of the tree")
        called = _called_names(path)
        # Either directly (the API process, which has its own lifespan) or
        # through the worker helper, whose `start()` launches both loops.
        reports = "run_process_adoption_reporter" in called or (
            "build_worker_runtime_activation" in called and "start" in called
        )
        assert reports, f"{process_class} ({path.name}) never reports its adopted release"


def test_the_worker_helper_starts_reconciliation_and_reporting_together() -> None:
    """One without the other is a process that lies or a release that never lands."""

    from return_platform.configuration.runtime_activation import WorkerRuntimeActivation

    source = __import__("inspect").getsource(WorkerRuntimeActivation.start)
    assert "run_runtime_activation_loop" in source
    assert "run_process_adoption_reporter" in source


def test_adoption_is_not_stored_in_the_class_keyed_heartbeat() -> None:
    """`worker_heartbeats` is `_id = <class>`, so it cannot hold two replicas."""

    from return_platform.operations.repository import WORKER_HEARTBEATS

    assert PROCESS_ADOPTIONS_COLLECTION != WORKER_HEARTBEATS


# --------------------------------------------------------------------------- #
# The operator surface
# --------------------------------------------------------------------------- #


@pytest.fixture
def adoption_client(test_settings: Settings) -> Any:
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from return_platform.configuration.api.router import router
    from return_platform.configuration.graph_repository import (
        InMemoryConfigurationGraphRepository,
    )
    from return_platform.security.principal import Principal

    app = FastAPI()
    app.include_router(router)
    app.state.settings = test_settings
    repository = InMemoryConfigurationGraphRepository()
    app.state.graph_configuration_repository = repository
    collection = _FakeCollection()
    app.state.process_adoption_store = MongoProcessAdoptionStore(collection)

    @app.middleware("http")
    async def attach_principal(request: Request, call_next: Any) -> Any:
        request.state.principal = Principal(
            subject="configuration-admin", roles=frozenset({"console_admin"})
        )
        request.state.correlation_id = "adoption-api-test"
        return await call_next(request)

    return TestClient(app), repository, app.state.process_adoption_store


@pytest.mark.asyncio
async def test_the_endpoint_reports_activating_until_every_class_has_adopted(
    adoption_client: Any,
) -> None:
    """The Configuration screen's answer, through the canonical surface.

    Activated comes from the graph, not from this process's own snapshot: the
    API is one of the adopters, and a handler answering from `app.state` would
    be structurally unable to report itself as behind.
    """

    from return_platform.configuration.snapshot import RETURN_PLATFORM_DOMAIN_KEY

    client, repository, store = adoption_client
    await repository.save_draft_domain(
        "release-live", RETURN_PLATFORM_DOMAIN_KEY, {"schema_version": "1"}, actor_id="admin"
    )
    await repository.promote_release("release-live", "VALIDATED", actor_id="admin")
    await repository.promote_release(
        "release-live", "RELEASED", actor_id="admin", expected_head_revision=0
    )
    head = await repository.get_head_revision()

    for process_class in sorted(REQUIRED_PROCESS_CLASSES - {"order-discovery-worker"}):
        await store.report(
            _record(process_class, release_id="release-live", head_revision=head), ttl_seconds=5
        )

    body = client.get("/api/config/adoption").json()["data"]
    assert body["status"] == "ACTIVATING"
    # snake_case, matching its sibling `/api/config/runtime`, which dumps
    # `PinnedConfigurationSnapshot` as-is. The camelCase models on this router
    # are the hand-written `SourceItem`/`AuditLog` shapes; a runtime state
    # document follows the runtime state document already there.
    assert body["activated_release_id"] == "release-live"
    assert body["pending_process_classes"] == ["order-discovery-worker"]

    await store.report(
        _record("order-discovery-worker", release_id="release-live", head_revision=head),
        ttl_seconds=5,
    )

    body = client.get("/api/config/adoption").json()["data"]
    assert body["status"] == "LIVE"
    assert body["pending_process_classes"] == []

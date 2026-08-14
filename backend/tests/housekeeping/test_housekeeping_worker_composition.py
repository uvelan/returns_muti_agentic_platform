"""The worker's entry point imports, and its cycle builds and runs.

`scripts/run_data_job_worker.py` raised `ImportError` at every container start for
weeks because nothing tested that it could start. It was in `compose.yaml`, it was
in the topology test's service list, and both facts only ever proved somebody had
written it down. This file is the difference for `run_housekeeping_worker.py`:
the module is imported, the real composition root is called, and the real
reclaimers it builds are exercised.

It also asserts the two decisions that are easy to get wrong later: that
`housekeeping-worker` is *not* a required process class (it is not deployed
everywhere, and a required class that is not always deployed leaves every release
at ACTIVATING forever), and that a release whose rules cannot be validated
reclaims nothing rather than reclaiming on unvalidated rules.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from return_platform.configuration.process_adoption import REQUIRED_PROCESS_CLASSES
from return_platform.configuration.return_configuration import (
    HousekeepingConfiguration,
    TemporalReclamationConfiguration,
)
from return_platform.configuration.settings import Settings
from return_platform.housekeeping.composition import build_housekeeping_cycle
from return_platform.housekeeping.cycle import HousekeepingCycle
from return_platform.housekeeping.probe_databases import RESOURCE_CLASS as PROBE_CLASS
from return_platform.housekeeping.temporal_executions import (
    RESOURCE_CLASS as TEMPORAL_CLASS,
)

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_housekeeping_worker.py"


def test_the_worker_entry_point_exists_and_imports() -> None:
    """The `data-job-worker` guard. An import error here is a container that
    cannot start, and this is the only place that would notice."""
    assert _SCRIPT.is_file(), _SCRIPT
    spec = importlib.util.spec_from_file_location("run_housekeeping_worker", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert callable(module._run)
    assert module._PROCESS_CLASS == "housekeeping-worker"


def test_housekeeping_worker_is_not_a_required_process_class() -> None:
    """Deliberate, and the reason is in `REQUIRED_PROCESS_CLASSES`' docstring.

    Two of its three reclaimers are gated to development and test, so a
    production deployment has a real reason not to run it -- and listing a class
    that is not always deployed would leave every release permanently ACTIVATING.
    """
    assert "housekeeping-worker" not in REQUIRED_PROCESS_CLASSES


def _settings(**overrides: Any) -> Settings:
    return Settings.model_construct(
        environment="test",
        mongo_database="return_platform",
        neo4j_database=None,
        sqlserver_database="return_platform",
        ai_studio_sqlserver_database=None,
        return_workflow_task_queue="return-platform-return-v1",
        order_discovery_workflow_task_queue="return-platform-order-discovery-v1",
        **overrides,
    )


class _NoExecutions:
    def list_workflows(self, query: str = "") -> Any:
        async def iterator() -> Any:
            if False:  # pragma: no cover - an empty async iterator
                yield None

        return iterator()

    def get_workflow_handle(self, *_: Any, **__: Any) -> Any:  # pragma: no cover
        raise AssertionError("nothing should be terminated")


@pytest.mark.asyncio
async def test_the_real_composition_root_builds_a_runnable_cycle() -> None:
    """Built from real `Settings` and a real configuration block, then run.

    Mongo and the Neo4j driver are absent, so the graph reclaimer reports itself
    unconfigured rather than raising -- which is what a worker with a partial
    dependency set must do.
    """
    cycle = build_housekeeping_cycle(
        settings_provider=_settings,
        configuration_provider=HousekeepingConfiguration,
        temporal_client=_NoExecutions(),  # type: ignore[arg-type]
        mongo=None,
        neo4j_driver=None,
    )
    assert isinstance(cycle, HousekeepingCycle)

    result = await cycle.run_once()

    assert [outcome.resource_class for outcome in result.outcomes] == [
        "temporal-execution",
        "graph-generation",
        "probe-database",
    ]
    assert result.reclaimed == 0
    temporal = next(o for o in result.outcomes if o.resource_class == TEMPORAL_CLASS)
    # Ran, with the default prefixes, and found nothing. Not skipped: that
    # distinction is what tells an operator the reclaimer is wired.
    assert temporal.ran is True


@pytest.mark.asyncio
async def test_a_release_that_would_widen_the_rule_reclaims_nothing_and_says_why() -> None:
    """The fail-closed path, end to end through the cycle.

    `""` as a reclaimable prefix matches every queue including the deployed ones.
    The reclaimer's constructor raises, the cycle records the reason, and no
    execution is touched -- rather than the pass proceeding on rules nobody
    validated.
    """

    def configuration() -> HousekeepingConfiguration:
        return HousekeepingConfiguration.model_construct(
            enabled=True,
            interval_seconds=900,
            temporal_executions=TemporalReclamationConfiguration.model_construct(
                enabled=True,
                reclaimable_task_queue_prefixes=("",),
                minimum_age_seconds=3_600,
                batch_limit=100,
            ),
            graph_generations=HousekeepingConfiguration().graph_generations,
            probe_databases=HousekeepingConfiguration().probe_databases,
        )

    cycle = build_housekeeping_cycle(
        settings_provider=_settings,
        configuration_provider=configuration,
        temporal_client=_NoExecutions(),  # type: ignore[arg-type]
        mongo=None,
        neo4j_driver=None,
    )
    result = await cycle.run_once()

    temporal = next(o for o in result.outcomes if o.resource_class == TEMPORAL_CLASS)
    assert temporal.ran is False
    assert "configuration rejected" in (temporal.skipped_reason or "")
    assert result.reclaimed == 0


@pytest.mark.asyncio
async def test_disabling_the_block_disables_every_reclaimer() -> None:
    cycle = build_housekeeping_cycle(
        settings_provider=_settings,
        configuration_provider=lambda: HousekeepingConfiguration(enabled=False),
        temporal_client=_NoExecutions(),  # type: ignore[arg-type]
        mongo=None,
        neo4j_driver=None,
    )
    result = await cycle.run_once()

    assert all(outcome.skipped_reason == "disabled" for outcome in result.outcomes)


@pytest.mark.asyncio
async def test_one_reclaimer_failing_does_not_stop_the_others() -> None:
    class _Exploding:
        async def reclaim_once(self) -> Any:
            raise RuntimeError("neo4j is unreachable")

    from return_platform.housekeeping.reclamation import ReclamationOutcome

    class _Working:
        async def reclaim_once(self) -> ReclamationOutcome:
            return ReclamationOutcome(resource_class="probe-database", reclaimed=2)

    cycle = HousekeepingCycle(
        (
            ("graph-generation", lambda: _Exploding()),  # type: ignore[return-value]
            (PROBE_CLASS, lambda: _Working()),  # type: ignore[return-value]
        )
    )
    result = await cycle.run_once()

    assert result.outcomes[0].ran is False
    assert "neo4j is unreachable" in (result.outcomes[0].skipped_reason or "")
    assert result.outcomes[1].reclaimed == 2


def test_the_default_configuration_block_is_loadable_and_conservative() -> None:
    """Defaults matter: an existing release carries no `housekeeping` block."""
    configuration = HousekeepingConfiguration()

    assert configuration.enabled is True
    assert configuration.temporal_executions.reclaimable_task_queue_prefixes == (
        "test-",
        "reasoning-",
    )
    # No default prefix may match a deployed queue, or the shipped defaults would
    # be the dangerous configuration this whole design refuses.
    for prefix in configuration.temporal_executions.reclaimable_task_queue_prefixes:
        assert not "return-platform-return-v1".startswith(prefix)
        assert not "return-platform-order-discovery-v1".startswith(prefix)
    assert configuration.probe_databases.name_suffixes == ("_probe",)
    assert not "return_platform".endswith(configuration.probe_databases.name_suffixes[0])

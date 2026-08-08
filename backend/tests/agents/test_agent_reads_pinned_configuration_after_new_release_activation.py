from unittest.mock import AsyncMock, MagicMock

import pytest

from return_platform.bootstrap.epoch import SimpleRuntimeEpoch
from return_platform.configuration.application.runtime_configuration import (
    RuntimeConfigurationHandleImpl,
)
from return_platform.configuration.application.snapshot import compute_checksum
from return_platform.configuration.domain.agents import AgentConfigNode, AgentsConfig
from return_platform.configuration.domain.ai import AiConfig
from return_platform.configuration.domain.features import FeaturesConfig
from return_platform.configuration.domain.graph import GraphConfig
from return_platform.configuration.domain.integrations import IntegrationsConfig
from return_platform.configuration.domain.modules import ModuleConfigNode, ModulesConfig
from return_platform.configuration.domain.platform import PlatformConfig
from return_platform.configuration.domain.release_model import RuntimeSnapshot
from return_platform.configuration.domain.sources import SourcesConfig
from return_platform.configuration.domain.system_store import SystemStoreConfig
from return_platform.configuration.domain.workflow import WorkflowConfig


def _snapshot() -> RuntimeSnapshot:
    return RuntimeSnapshot(
        platform=PlatformConfig(),
        system_store=SystemStoreConfig(),
        modules=ModulesConfig(
            modules={
                "agent.order_discovery": ModuleConfigNode(
                    module_id="agent.order_discovery", module_type="AGENT"
                )
            }
        ),
        agents=AgentsConfig(agents={"order_discovery": AgentConfigNode()}),
        workflow=WorkflowConfig(workflow={}),
        sources=SourcesConfig(sources={}),
        integrations=IntegrationsConfig(integrations={}),
        graph=GraphConfig(),
        ai=AiConfig(),
        features=FeaturesConfig(flags={}),
    )


@pytest.mark.asyncio
async def test_agent_reads_pinned_configuration_after_new_release_activation():
    snapshot = _snapshot()
    checksum = compute_checksum(snapshot)

    client = MagicMock()
    db = MagicMock()
    client.get_database.return_value = db

    releases_coll = MagicMock()
    releases_coll.find_one = AsyncMock(
        return_value={
            "release_id": "r1",
            "snapshot": snapshot.model_dump(),
            "checksum": checksum,
        }
    )
    db.get_collection.return_value = releases_coll

    # RuntimeConfigurationHandleImpl.pinned() recomputes the checksum from the
    # loaded snapshot and fails closed on mismatch, so the loader must return
    # a real RuntimeSnapshot -- not a passthrough dict -- for the checksum
    # comparison to be meaningful.
    handle = RuntimeConfigurationHandleImpl(client, RuntimeSnapshot.model_validate)

    handle.set_current(SimpleRuntimeEpoch(2, "r2"), MagicMock(release_id="r2"))

    view = await handle.pinned("r1")
    assert view.release_id == "r1"

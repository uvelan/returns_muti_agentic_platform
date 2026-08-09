from unittest.mock import AsyncMock, MagicMock

import pytest

from return_platform.bootstrap.epoch import SimpleRuntimeEpoch
from return_platform.configuration.application.runtime_configuration import (
    RuntimeConfigurationHandleImpl,
)


@pytest.mark.asyncio
async def test_running_workflow_never_reads_current_release():
    client = MagicMock()
    db = MagicMock()
    client.get_database.return_value = db

    releases_coll = MagicMock()
    releases_coll.find_one = AsyncMock(
        return_value={"release_id": "r1", "snapshot": {}, "checksum": "c1"}
    )
    db.get_collection.return_value = releases_coll

    handle = RuntimeConfigurationHandleImpl(client, lambda x: x)

    for i in range(1, 10):
        handle.set_current(SimpleRuntimeEpoch(i, f"r{i}"), MagicMock(release_id=f"r{i}"))

    view = await handle.pinned("r1")
    assert view.release_id == "r1"

from unittest.mock import AsyncMock, MagicMock

import pytest

from return_platform.configuration.application.runtime_configuration import (
    RuntimeConfigurationHandleImpl,
)


@pytest.mark.asyncio
async def test_pinned_release_resolves_after_handle_recreation():
    client = MagicMock()
    db = MagicMock()
    client.get_database.return_value = db

    releases_coll = MagicMock()
    releases_coll.find_one = AsyncMock(
        return_value={
            "release_id": "r1",
            "snapshot": {},
            "checksum": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
        }
    )
    db.get_collection.return_value = releases_coll

    def load_snapshot_fn(snap):
        mock_snap = MagicMock()
        mock_snap.model_dump.return_value = {}
        return mock_snap

    handle1 = RuntimeConfigurationHandleImpl(client, load_snapshot_fn)
    handle2 = RuntimeConfigurationHandleImpl(client, load_snapshot_fn)

    assert not handle2._views

    view = await handle2.pinned("r1")
    assert view.release_id == "r1"
    assert view.checksum == "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    assert "r1" in handle2._views

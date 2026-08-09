from unittest.mock import AsyncMock, MagicMock

import pytest

from return_platform.bootstrap.reconciler import ConfigurationReconciler


@pytest.mark.asyncio
async def test_reconciler_delegates_to_reconfiguration_coordinator():
    coordinator = AsyncMock()
    coordinator.reconfigure.return_value = 1

    epoch_allocator = MagicMock()
    epoch_allocator.next.return_value = MagicMock(epoch=2, release_id="r2")

    handle = MagicMock()

    def load_snapshot_fn(snap):
        return snap

    client = MagicMock()
    db = MagicMock()
    client.get_database.return_value = db

    releases_coll = MagicMock()
    releases_coll.find_one = AsyncMock(
        return_value={"release_id": "r2", "snapshot": {}, "checksum": "c2"}
    )

    db.get_collection.side_effect = lambda name: (
        releases_coll if name == "configuration_releases" else MagicMock()
    )

    reconciler = ConfigurationReconciler(
        client, "i1", coordinator, epoch_allocator, handle, load_snapshot_fn
    )

    reconciler._db = db

    await reconciler._adopt_release("r2", "c2")

    coordinator.reconfigure.assert_called_once()
    handle.set_current.assert_called_once()

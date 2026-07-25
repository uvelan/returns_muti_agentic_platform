from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from return_platform.operations.repository import OperationalRepository


@pytest.mark.asyncio
async def test_event_deduplication_index_replaces_legacy_sparse_index() -> None:
    repository = cast(Any, object.__new__(OperationalRepository))
    events = SimpleNamespace(
        index_information=AsyncMock(
            return_value={
                "_id_": {"key": [("_id", 1)]},
                "streamId_1_deduplicationKey_1": {
                    "key": [("streamId", 1), ("deduplicationKey", 1)],
                    "unique": True,
                    "sparse": True,
                },
            }
        ),
        drop_index=AsyncMock(),
        create_index=AsyncMock(),
    )
    repository.events = events

    await repository._ensure_event_deduplication_index()

    events.drop_index.assert_awaited_once_with("streamId_1_deduplicationKey_1")
    events.create_index.assert_awaited_once_with(
        [("streamId", 1), ("deduplicationKey", 1)],
        name="stream_deduplication_unique",
        unique=True,
        partialFilterExpression={"deduplicationKey": {"$type": "string"}},
    )


@pytest.mark.asyncio
async def test_event_deduplication_index_keeps_current_partial_index() -> None:
    repository = cast(Any, object.__new__(OperationalRepository))
    events = SimpleNamespace(
        index_information=AsyncMock(
            return_value={
                "stream_deduplication_unique": {
                    "key": [("streamId", 1), ("deduplicationKey", 1)],
                    "unique": True,
                    "partialFilterExpression": {
                        "deduplicationKey": {"$type": "string"}
                    },
                }
            }
        ),
        drop_index=AsyncMock(),
        create_index=AsyncMock(),
    )
    repository.events = events

    await repository._ensure_event_deduplication_index()

    events.drop_index.assert_not_awaited()
    events.create_index.assert_not_awaited()

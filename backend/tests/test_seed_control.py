from __future__ import annotations

import pytest
from pydantic import ValidationError

from return_platform.operations.models import SeedDeleteRequest, SeedOperationStatus
from return_platform.operations.seed_control import (
    SeedOperationCancelled,
    SeedOperationControl,
)


@pytest.mark.asyncio
async def test_seed_operation_tracks_progress_and_cancellation() -> None:
    control = SeedOperationControl()
    operation_id = await control.begin(
        kind="APPLY",
        record_limit=1_000,
        total_records=7_000,
    )

    await control.update(
        operation_id,
        processed_delta=1_000,
        phase="Writing orders",
    )
    running = await control.snapshot()
    assert running.status is SeedOperationStatus.RUNNING
    assert running.processedRecords == 1_000
    assert running.phase == "Writing orders"

    cancelling = await control.request_cancel()
    assert cancelling.status is SeedOperationStatus.CANCELLING
    with pytest.raises(SeedOperationCancelled):
        control.raise_if_cancelled(operation_id)

    await control.finish(
        operation_id,
        SeedOperationStatus.CANCELLED,
        phase="Stopped by user",
    )
    stopped = await control.snapshot()
    assert stopped.status is SeedOperationStatus.CANCELLED
    assert stopped.finishedAt is not None


@pytest.mark.asyncio
async def test_seed_operation_rejects_concurrent_mutations() -> None:
    control = SeedOperationControl()
    await control.begin(kind="APPLY", record_limit=100, total_records=700)

    with pytest.raises(RuntimeError, match="already running"):
        await control.begin(kind="DELETE", record_limit=None, total_records=0)


def test_seed_delete_requires_explicit_confirmation() -> None:
    assert SeedDeleteRequest(confirmation="DELETE SEED DATA").confirmation == ("DELETE SEED DATA")
    with pytest.raises(ValidationError):
        SeedDeleteRequest(confirmation="delete")

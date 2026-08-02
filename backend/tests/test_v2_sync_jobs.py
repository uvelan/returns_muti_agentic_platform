import pytest

from return_platform.v2.models import (
    AuthorizationScope,
    OrderAnchor,
    PartialSyncRequest,
    SourceOrderRecord,
)
from return_platform.v2.services import (
    InMemoryOrderProjectionStore,
    InMemoryOrderSourceGateway,
    OrderSyncService,
    V2ConflictError,
)
from return_platform.v2.sync_jobs import (
    DurableOrderSyncCoordinator,
    JobClaimRequest,
    SyncJobStatus,
)


def _request() -> PartialSyncRequest:
    return PartialSyncRequest(
        anchor=OrderAnchor(type="TRACKING_NUMBER", value="TRACK-100"),
        release_id="release-active",
        authorization_scope=AuthorizationScope(accounts=("ACCOUNT1",)),
        idempotency_key="durable-partial-account1-100",
    )


def _coordinator() -> DurableOrderSyncCoordinator:
    source = InMemoryOrderSourceGateway(
        (
            SourceOrderRecord(
                account="ACCOUNT1",
                order_number="ORDER100",
                tracking_numbers=("TRACK-100",),
                source_revision="rev-1",
                lines=({"lineNumber": "1", "itemNumber": "SKU-1"},),
            ),
        )
    )
    return DurableOrderSyncCoordinator(
        OrderSyncService(source, InMemoryOrderProjectionStore())
    )


@pytest.mark.asyncio
async def test_job_enqueue_claim_execute_and_replay_are_idempotent() -> None:
    coordinator = _coordinator()

    first = await coordinator.enqueue_partial(_request())
    replay = await coordinator.enqueue_partial(_request())
    claimed = await coordinator.claim(JobClaimRequest(worker_id="worker-1", lease_seconds=30))

    assert first.job_id == replay.job_id
    assert claimed is not None
    assert claimed.job_id == first.job_id
    assert claimed.status is SyncJobStatus.RUNNING
    assert claimed.attempts == 1

    completed, result = await coordinator.execute(first.job_id, "worker-1")

    assert completed.status is SyncJobStatus.COMPLETED
    assert completed.lease_owner is None
    assert result is not None
    assert result.full_order_ids == ("ACCOUNT1*ORDER100",)
    assert completed.result_request_id == result.request_id
    assert await coordinator.claim(JobClaimRequest(worker_id="worker-2")) is None


@pytest.mark.asyncio
async def test_job_lease_rejects_another_worker() -> None:
    coordinator = _coordinator()
    job = await coordinator.enqueue_partial(_request())
    claimed = await coordinator.claim(JobClaimRequest(worker_id="worker-1"))
    assert claimed is not None

    with pytest.raises(V2ConflictError, match="another worker"):
        await coordinator.execute(job.job_id, "worker-2")


@pytest.mark.asyncio
async def test_job_snapshot_restores_queue_and_idempotency_key() -> None:
    coordinator = _coordinator()
    job = await coordinator.enqueue_partial(_request())
    snapshot = coordinator.snapshot()
    restarted = _coordinator()

    restarted.restore(snapshot)
    replay = await restarted.enqueue_partial(_request())

    assert replay.job_id == job.job_id
    assert (await restarted.get(job.job_id)).status is SyncJobStatus.QUEUED

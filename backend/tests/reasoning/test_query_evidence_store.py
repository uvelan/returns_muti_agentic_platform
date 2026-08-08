"""QueryEvidenceStore: durable, encrypted, insert-once evidence-by-reference
storage -- the checkpoint holds only query_execution_id, this holds the full
QueryEvidence (including its raw result)."""

from __future__ import annotations

import pytest

from return_platform.dynamic_knowledge.knowledge.evidence import QueryEvidence
from return_platform.platform.reasoning.evidence_store import QueryEvidenceStore
from tests.reasoning.conftest import ReasoningTestFixture


def _evidence(query_execution_id: str, result: object) -> QueryEvidence:
    return QueryEvidence.create(
        query_execution_id=query_execution_id,
        schema_version="2026.08.1",
        graph_generation_id="legacy-live",
        logical_plan_checksum="a" * 64,
        compiled_query_checksum="b" * 64,
        result=result,
    )


@pytest.mark.asyncio
async def test_put_and_get_round_trips_the_full_evidence(
    reasoning_store: ReasoningTestFixture,
) -> None:
    store = QueryEvidenceStore(reasoning_store.store, reasoning_store.encryptor)
    evidence = _evidence("qe-round-trip", {"orderId": "ORD-1", "lines": [{"sku": "A-1"}]})
    await store.put(run_id="run-1", evidence=evidence)

    rehydrated = await store.get("qe-round-trip")
    assert rehydrated is not None
    assert rehydrated.result == {"orderId": "ORD-1", "lines": [{"sku": "A-1"}]}
    assert rehydrated.query_execution_id == "qe-round-trip"
    assert rehydrated.result_checksum == evidence.result_checksum


@pytest.mark.asyncio
async def test_get_returns_none_for_an_unknown_id(reasoning_store: ReasoningTestFixture) -> None:
    store = QueryEvidenceStore(reasoning_store.store, reasoning_store.encryptor)
    assert await store.get("no-such-evidence") is None


@pytest.mark.asyncio
async def test_get_many_rehydrates_in_the_requested_order(
    reasoning_store: ReasoningTestFixture,
) -> None:
    store = QueryEvidenceStore(reasoning_store.store, reasoning_store.encryptor)
    await store.put(run_id="run-2", evidence=_evidence("qe-a", {"v": "A"}))
    await store.put(run_id="run-2", evidence=_evidence("qe-b", {"v": "B"}))

    evidence = await store.get_many(["qe-b", "qe-a"])
    assert [item.query_execution_id for item in evidence] == ["qe-b", "qe-a"]
    assert [item.result for item in evidence] == [{"v": "B"}, {"v": "A"}]


@pytest.mark.asyncio
async def test_get_many_raises_on_a_dangling_reference(
    reasoning_store: ReasoningTestFixture,
) -> None:
    store = QueryEvidenceStore(reasoning_store.store, reasoning_store.encryptor)
    await store.put(run_id="run-3", evidence=_evidence("qe-real", {"v": "A"}))
    with pytest.raises(KeyError):
        await store.get_many(["qe-real", "qe-missing"])


@pytest.mark.asyncio
async def test_put_is_idempotent_for_a_retried_write_of_the_same_run(
    reasoning_store: ReasoningTestFixture,
) -> None:
    store = QueryEvidenceStore(reasoning_store.store, reasoning_store.encryptor)
    evidence = _evidence("qe-retry", {"v": "A"})
    await store.put(run_id="run-4", evidence=evidence)
    # A crashed node re-executed after a checkpoint resume writes the same
    # query_execution_id again -- must not raise a duplicate-key error.
    await store.put(run_id="run-4", evidence=evidence)

    rehydrated = await store.get("qe-retry")
    assert rehydrated is not None
    assert rehydrated.result == {"v": "A"}


@pytest.mark.asyncio
async def test_put_rejects_a_retry_under_a_different_run_id(
    reasoning_store: ReasoningTestFixture,
) -> None:
    store = QueryEvidenceStore(reasoning_store.store, reasoning_store.encryptor)
    await store.put(run_id="run-5", evidence=_evidence("qe-conflict", {"v": "A"}))
    with pytest.raises(ValueError, match="already recorded"):
        await store.put(run_id="run-6", evidence=_evidence("qe-conflict", {"v": "A"}))

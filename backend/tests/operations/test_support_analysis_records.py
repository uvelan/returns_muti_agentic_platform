"""S2: the analysis of a support event is a fact, not a coincidence.

Contracts.md sect. 5. One record per `support_event_id`; each stage's routing
decision pinned before the first invocation and never re-pinned; attempts
beneath the record naming their route; exactly one CAS-accepted result per
stage that a retry reuses rather than re-deriving; artifact writes gated on the
committed `accepted_extraction`; every candidate exhausted -> block and
dead-letter rather than a silent empty answer.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import pytest
import pytest_asyncio

from return_platform.configuration.settings import Settings
from return_platform.operations.return_support.analysis_records import (
    ANALYSIS_EVENT_INDEX,
    SUPPORT_ANALYSIS_RECORDS,
    AnalysisRecordNotFoundError,
    AnalysisStage,
    AnalysisStatus,
    ArtifactWriteBlockedError,
    CandidateRoutesExhaustedError,
    RouteNotPinnedError,
    RoutingNotPinnedError,
    SupportAnalysisRecordStore,
    ensure_support_analysis_indexes,
    require_accepted_extraction,
)
from tests.operations.mongo_double import FakeClient, FakeCollection

CASE_ID = "case-9300"
EVENT_ID = "support-event-1"
ROUTES = ("primary-model", "secondary-model", "fallback-model")
RELEASE = "release-2026-08-01"
POLICY = "routing-policy-v4"


@pytest.fixture
def mongo() -> FakeClient:
    return FakeClient()


@pytest.fixture
def records(mongo: FakeClient, test_settings: Settings) -> FakeCollection:
    return mongo[test_settings.mongo_database][SUPPORT_ANALYSIS_RECORDS]


@pytest_asyncio.fixture
async def store(mongo: FakeClient, test_settings: Settings) -> SupportAnalysisRecordStore:
    await ensure_support_analysis_indexes(cast(Any, mongo[test_settings.mongo_database]))
    return SupportAnalysisRecordStore(cast(Any, mongo), test_settings)


async def _pinned(
    store: SupportAnalysisRecordStore,
    *,
    stage: AnalysisStage = AnalysisStage.CLASSIFICATION,
    routes: tuple[str, ...] = ROUTES,
) -> dict[str, Any]:
    await store.ensure_record(case_id=CASE_ID, support_event_id=EVENT_ID)
    return await store.pin_routing_decision(
        support_event_id=EVENT_ID,
        stage=stage,
        release_id=RELEASE,
        routing_policy_version=POLICY,
        ordered_candidate_routes=routes,
    )


# --------------------------------------------------------------------------- #
# One record per event
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_one_record_per_support_event(
    store: SupportAnalysisRecordStore, records: FakeCollection
) -> None:
    """Definition of done: a fallback attempt never mints a second record.

    Two records would be two analyses of one message with nothing to say which
    the case believes.
    """
    first = await store.ensure_record(case_id=CASE_ID, support_event_id=EVENT_ID)
    second = await store.ensure_record(case_id=CASE_ID, support_event_id=EVENT_ID)

    assert first["_id"] == second["_id"]
    assert len(records.documents) == 1
    assert first["status"] == AnalysisStatus.PENDING.value


@pytest.mark.asyncio
async def test_the_event_index_is_unique(
    store: SupportAnalysisRecordStore, records: FakeCollection
) -> None:
    del store
    declaration = next(
        options
        for _keys, options in records.index_calls
        if options.get("name") == ANALYSIS_EVENT_INDEX
    )
    assert declaration["unique"] is True


@pytest.mark.asyncio
async def test_the_race_to_create_leaves_one_record(
    store: SupportAnalysisRecordStore, records: FakeCollection
) -> None:
    """The pre-check cannot see a concurrent creator; the index can."""
    winner = await store.ensure_record(case_id=CASE_ID, support_event_id=EVENT_ID)

    original = records.find_one
    blinded = {"done": False}

    async def blind_once(query: Any, **kwargs: Any) -> Any:
        if not blinded["done"]:
            blinded["done"] = True
            return None
        return await original(query, **kwargs)

    records.find_one = blind_once  # type: ignore[method-assign]
    try:
        loser = await store.ensure_record(case_id=CASE_ID, support_event_id=EVENT_ID)
    finally:
        records.find_one = original  # type: ignore[method-assign]

    assert loser["_id"] == winner["_id"]
    assert len(records.documents) == 1


@pytest.mark.asyncio
async def test_reading_an_unknown_event_raises(
    store: SupportAnalysisRecordStore,
) -> None:
    with pytest.raises(AnalysisRecordNotFoundError):
        await store.get("no-such-event")
    assert await store.find("no-such-event") is None


# --------------------------------------------------------------------------- #
# The pinned routing decision
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_routing_decision_pins_release_policy_and_ordered_candidates(
    store: SupportAnalysisRecordStore,
) -> None:
    decision = await _pinned(store)

    assert decision["release_id"] == RELEASE
    assert decision["routing_policy_version"] == POLICY
    assert decision["ordered_candidate_routes"] == list(ROUTES)

    record = await store.get(EVENT_ID)
    assert record["classification_release_id"] == RELEASE
    assert record["classification_routing_decision"]["ordered_candidate_routes"] == list(ROUTES)


@pytest.mark.asyncio
async def test_re_pinning_keeps_the_first_decision(
    store: SupportAnalysisRecordStore,
) -> None:
    """Idempotent by keeping, not by overwriting.

    A re-pin under a newer policy would silently change what the attempts
    already recorded were attempts at.
    """
    first = await _pinned(store)

    second = await store.pin_routing_decision(
        support_event_id=EVENT_ID,
        stage=AnalysisStage.CLASSIFICATION,
        release_id="release-2026-09-01",
        routing_policy_version="routing-policy-v5",
        ordered_candidate_routes=("some-other-model",),
    )

    assert second["release_id"] == first["release_id"] == RELEASE
    assert second["routing_policy_version"] == POLICY
    assert second["ordered_candidate_routes"] == list(ROUTES)


@pytest.mark.asyncio
async def test_the_two_stages_pin_independently(
    store: SupportAnalysisRecordStore,
) -> None:
    """Classification and extraction are separate decisions on one record."""
    await _pinned(store)
    extraction = await store.pin_routing_decision(
        support_event_id=EVENT_ID,
        stage=AnalysisStage.EXTRACTION,
        release_id="release-extract-1",
        routing_policy_version=POLICY,
        ordered_candidate_routes=("extract-primary",),
    )

    record = await store.get(EVENT_ID)
    assert extraction["release_id"] == "release-extract-1"
    assert record["classification_release_id"] == RELEASE
    assert record["extraction_release_id"] == "release-extract-1"


@pytest.mark.asyncio
async def test_an_empty_candidate_list_is_refused_at_the_pin(
    store: SupportAnalysisRecordStore,
) -> None:
    """An empty list is a block, and should be recorded as one."""
    await store.ensure_record(case_id=CASE_ID, support_event_id=EVENT_ID)
    with pytest.raises(ValueError, match="at least one candidate route"):
        await store.pin_routing_decision(
            support_event_id=EVENT_ID,
            stage=AnalysisStage.CLASSIFICATION,
            release_id=RELEASE,
            routing_policy_version=POLICY,
            ordered_candidate_routes=(),
        )


@pytest.mark.asyncio
async def test_nothing_may_be_attempted_before_the_pin(
    store: SupportAnalysisRecordStore,
) -> None:
    """Pinning after invoking would record which provider answered, not which
    providers were eligible -- and only the second is auditable."""
    await store.ensure_record(case_id=CASE_ID, support_event_id=EVENT_ID)
    record = await store.get(EVENT_ID)

    with pytest.raises(RoutingNotPinnedError):
        store.routing_decision(record, AnalysisStage.CLASSIFICATION)
    with pytest.raises(RoutingNotPinnedError):
        store.next_candidate_route(record, AnalysisStage.CLASSIFICATION)
    with pytest.raises(RoutingNotPinnedError):
        await store.record_attempt(
            support_event_id=EVENT_ID,
            stage=AnalysisStage.CLASSIFICATION,
            route_id=ROUTES[0],
            outcome="UNAVAILABLE",
        )


# --------------------------------------------------------------------------- #
# Attempts and the candidate ladder
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_attempts_walk_the_pinned_order_and_stop_at_its_end(
    store: SupportAnalysisRecordStore,
) -> None:
    await _pinned(store)

    for expected in ROUTES:
        record = await store.get(EVENT_ID)
        assert store.next_candidate_route(record, AnalysisStage.CLASSIFICATION) == expected
        await store.record_attempt(
            support_event_id=EVENT_ID,
            stage=AnalysisStage.CLASSIFICATION,
            route_id=expected,
            outcome="UNAVAILABLE",
        )

    record = await store.get(EVENT_ID)
    assert store.next_candidate_route(record, AnalysisStage.CLASSIFICATION) is None
    assert len(record["attempts"]) == len(ROUTES)


@pytest.mark.asyncio
async def test_each_attempt_names_its_route_release_and_policy(
    store: SupportAnalysisRecordStore,
) -> None:
    """The trail has to say which provider produced which answer."""
    await _pinned(store)
    attempt = await store.record_attempt(
        support_event_id=EVENT_ID,
        stage=AnalysisStage.CLASSIFICATION,
        route_id=ROUTES[0],
        outcome="ACCEPTED",
        detail={"latencyMs": 812},
    )

    assert attempt["routeId"] == ROUTES[0]
    assert attempt["releaseId"] == RELEASE
    assert attempt["routingPolicyVersion"] == POLICY
    assert attempt["detail"] == {"latencyMs": 812}


@pytest.mark.asyncio
async def test_an_attempt_on_an_unpinned_route_is_refused(
    store: SupportAnalysisRecordStore,
) -> None:
    """It means the caller routed by something other than the decision."""
    await _pinned(store)

    with pytest.raises(RouteNotPinnedError):
        await store.record_attempt(
            support_event_id=EVENT_ID,
            stage=AnalysisStage.CLASSIFICATION,
            route_id="a-model-nobody-pinned",
            outcome="ACCEPTED",
        )


@pytest.mark.asyncio
async def test_the_ladder_is_per_stage(store: SupportAnalysisRecordStore) -> None:
    """Exhausting classification's candidates says nothing about extraction's."""
    await _pinned(store)
    await store.pin_routing_decision(
        support_event_id=EVENT_ID,
        stage=AnalysisStage.EXTRACTION,
        release_id=RELEASE,
        routing_policy_version=POLICY,
        ordered_candidate_routes=("extract-primary",),
    )
    for route in ROUTES:
        await store.record_attempt(
            support_event_id=EVENT_ID,
            stage=AnalysisStage.CLASSIFICATION,
            route_id=route,
            outcome="UNAVAILABLE",
        )

    record = await store.get(EVENT_ID)
    assert store.next_candidate_route(record, AnalysisStage.CLASSIFICATION) is None
    assert store.next_candidate_route(record, AnalysisStage.EXTRACTION) == "extract-primary"


# --------------------------------------------------------------------------- #
# Acceptance
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_first_accepted_result_is_the_analysis_and_stays_it(
    store: SupportAnalysisRecordStore,
) -> None:
    """A retry reuses the accepted result; it never re-derives one.

    The second answer is not merged, not preferred for being newer, and not
    recorded. The first accepted answer is the analysis.
    """
    await _pinned(store)
    accepted, is_new = await store.accept_result(
        support_event_id=EVENT_ID,
        stage=AnalysisStage.CLASSIFICATION,
        route_id=ROUTES[0],
        result={"intent": "rma_issued"},
    )
    assert is_new is True
    assert accepted["intent"] == "rma_issued"
    assert accepted["route_id"] == ROUTES[0]
    assert accepted["release_id"] == RELEASE

    reused, is_new_again = await store.accept_result(
        support_event_id=EVENT_ID,
        stage=AnalysisStage.CLASSIFICATION,
        route_id=ROUTES[1],
        result={"intent": "rejection"},
    )
    assert is_new_again is False
    assert reused["intent"] == "rma_issued"
    assert reused["route_id"] == ROUTES[0]


@pytest.mark.asyncio
async def test_a_concurrent_acceptance_loses_to_the_committed_one(
    store: SupportAnalysisRecordStore,
) -> None:
    """The CAS, with the pre-read blinded -- the race the read cannot see."""
    await _pinned(store)
    winner, _ = await store.accept_result(
        support_event_id=EVENT_ID,
        stage=AnalysisStage.CLASSIFICATION,
        route_id=ROUTES[0],
        result={"intent": "rma_issued"},
    )

    original = store._records.find_one_and_update  # noqa: SLF001

    async def cas_that_loses(query: Any, *args: Any, **kwargs: Any) -> Any:
        if "accepted_classification" in query:
            return None
        return await original(query, *args, **kwargs)

    store._records.find_one_and_update = cas_that_loses  # type: ignore[method-assign]  # noqa: SLF001
    try:
        # Blind the pre-read too, so the call reaches the CAS.
        record = await store.get(EVENT_ID)
        record["accepted_classification"] = None
        loser, is_new = await store.accept_result(
            support_event_id=EVENT_ID,
            stage=AnalysisStage.CLASSIFICATION,
            route_id=ROUTES[1],
            result={"intent": "rejection"},
        )
    finally:
        store._records.find_one_and_update = original  # type: ignore[method-assign]  # noqa: SLF001

    assert is_new is False
    assert loser["intent"] == winner["intent"] == "rma_issued"


@pytest.mark.asyncio
async def test_accepting_classification_then_extraction_walks_the_status(
    store: SupportAnalysisRecordStore,
) -> None:
    await _pinned(store)
    await store.pin_routing_decision(
        support_event_id=EVENT_ID,
        stage=AnalysisStage.EXTRACTION,
        release_id=RELEASE,
        routing_policy_version=POLICY,
        ordered_candidate_routes=("extract-primary",),
    )

    await store.accept_result(
        support_event_id=EVENT_ID,
        stage=AnalysisStage.CLASSIFICATION,
        route_id=ROUTES[0],
        result={"intent": "rma_issued"},
    )
    assert (await store.get(EVENT_ID))["status"] == AnalysisStatus.CLASSIFIED.value

    await store.accept_result(
        support_event_id=EVENT_ID,
        stage=AnalysisStage.EXTRACTION,
        route_id="extract-primary",
        result={"entities": {"rmaNumber": "RMA-42"}},
    )
    assert (await store.get(EVENT_ID))["status"] == AnalysisStatus.COMPLETE.value


@pytest.mark.asyncio
async def test_accepting_on_an_unpinned_route_is_refused(
    store: SupportAnalysisRecordStore,
) -> None:
    await _pinned(store)
    with pytest.raises(RouteNotPinnedError):
        await store.accept_result(
            support_event_id=EVENT_ID,
            stage=AnalysisStage.CLASSIFICATION,
            route_id="a-model-nobody-pinned",
            result={"intent": "other"},
        )


# --------------------------------------------------------------------------- #
# The artifact-write gate
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_artifacts_cannot_be_written_before_the_extraction_is_accepted(
    store: SupportAnalysisRecordStore,
) -> None:
    """Artifacts written from an attempt would sit on the case beside the
    winner's, with nothing to say which extraction the case's data came from."""
    await _pinned(store, stage=AnalysisStage.EXTRACTION, routes=("extract-primary",))
    record = await store.get(EVENT_ID)

    with pytest.raises(ArtifactWriteBlockedError):
        require_accepted_extraction(record)

    # An accepted *classification* is not an accepted extraction.
    await store.pin_routing_decision(
        support_event_id=EVENT_ID,
        stage=AnalysisStage.CLASSIFICATION,
        release_id=RELEASE,
        routing_policy_version=POLICY,
        ordered_candidate_routes=ROUTES,
    )
    await store.accept_result(
        support_event_id=EVENT_ID,
        stage=AnalysisStage.CLASSIFICATION,
        route_id=ROUTES[0],
        result={"intent": "rma_issued"},
    )
    with pytest.raises(ArtifactWriteBlockedError):
        require_accepted_extraction(await store.get(EVENT_ID))


@pytest.mark.asyncio
async def test_the_gate_opens_on_the_committed_extraction_and_returns_it(
    store: SupportAnalysisRecordStore,
) -> None:
    """The gate and the source of the data are one call, so they cannot drift."""
    await _pinned(store, stage=AnalysisStage.EXTRACTION, routes=("extract-primary",))
    await store.accept_result(
        support_event_id=EVENT_ID,
        stage=AnalysisStage.EXTRACTION,
        route_id="extract-primary",
        result={"entities": {"rmaNumber": "RMA-42"}},
    )

    extraction = require_accepted_extraction(await store.get(EVENT_ID))
    assert extraction["entities"] == {"rmaNumber": "RMA-42"}
    assert extraction["route_id"] == "extract-primary"


# --------------------------------------------------------------------------- #
# The block
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_all_candidates_unavailable_blocks_and_dead_letters(
    store: SupportAnalysisRecordStore, caplog: pytest.LogCaptureFixture
) -> None:
    """Definition of done: block, not a silent empty result.

    An empty extraction would read downstream as "the message contained
    nothing", which is a different and much worse claim than "nobody was able
    to read the message".
    """
    await _pinned(store)
    for route in ROUTES:
        await store.record_attempt(
            support_event_id=EVENT_ID,
            stage=AnalysisStage.CLASSIFICATION,
            route_id=route,
            outcome="UNAVAILABLE",
        )
    record = await store.get(EVENT_ID)
    assert store.next_candidate_route(record, AnalysisStage.CLASSIFICATION) is None

    with caplog.at_level(logging.ERROR, logger="return_platform.support_analysis"):
        with pytest.raises(CandidateRoutesExhaustedError) as raised:
            await store.block_exhausted(
                support_event_id=EVENT_ID, stage=AnalysisStage.CLASSIFICATION
            )

    assert raised.value.tried == ROUTES
    assert any(item.message == "support_analysis_blocked" for item in caplog.records)

    blocked = await store.get(EVENT_ID)
    assert blocked["status"] == AnalysisStatus.BLOCKED.value
    assert blocked["blockReason"]["stage"] == AnalysisStage.CLASSIFICATION.value
    assert blocked["blockReason"]["triedRoutes"] == list(ROUTES)
    assert blocked["blockReason"]["reason"] == "ALL_CANDIDATE_ROUTES_UNAVAILABLE"


@pytest.mark.asyncio
async def test_the_block_is_durable_before_the_exception_is_raised(
    store: SupportAnalysisRecordStore,
) -> None:
    """The block is what puts the event on the operations surface; it must
    survive whatever the caller does with the exception."""
    await _pinned(store)
    await store.record_attempt(
        support_event_id=EVENT_ID,
        stage=AnalysisStage.CLASSIFICATION,
        route_id=ROUTES[0],
        outcome="UNAVAILABLE",
    )
    try:
        await store.block_exhausted(
            support_event_id=EVENT_ID,
            stage=AnalysisStage.CLASSIFICATION,
            reason="PROVIDER_QUOTA_EXHAUSTED",
        )
    except CandidateRoutesExhaustedError:
        pass

    listed = await store.list_blocked(CASE_ID)
    assert [item["supportEventId"] for item in listed] == [EVENT_ID]
    assert listed[0]["blockReason"]["reason"] == "PROVIDER_QUOTA_EXHAUSTED"

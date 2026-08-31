"""V2: the relay to Channel A, and the wiring that plugs the analyser in.

Two guarantees carry this file:

* **the system entry is not a turn** -- appending one must leave the Order
  Discovery agent's turn-based contract byte-identical, and must be idempotent
  across the dispatcher's at-least-once redelivery;
* **a blocked analysis dead-letters, an outage retries** -- getting that
  backwards turns a permanently-unanalysable message into an infinite retry
  nobody is watching, or a five-minute provider outage into a dead letter.
"""

from __future__ import annotations

from typing import Any

import pytest

from return_platform.dynamic_knowledge.order_agent.conversation_repository import (
    ConversationScope,
    _transcript_of,
)
from return_platform.operations.integrations.outbox import (
    OutboxCommand,
    PermanentDeliveryFailure,
    TransientDeliveryFailure,
)
from return_platform.operations.return_support.analysis_records import (
    AnalysisStage,
    CandidateRoutesExhaustedError,
)
from return_platform.operations.return_support.analysis_wiring import (
    ANALYSIS_BLOCKED,
    UNKNOWN_SUPPORT_EVENT,
    StructuredStageInvoker,
    SupportAnalysisEnvelope,
    SupportMessageClassifyDispatcher,
)
from return_platform.operations.return_support.message_classification import (
    SUPPORT_UPDATE_ENTRY_KIND,
    AnalysisOutcome,
    RouteUnavailableError,
)
from return_platform.operations.return_support.relay import (
    SYSTEM_ENTRIES_KEY,
    SupportTranscriptRelay,
    system_entry_id,
)

CASE_ID = "case-77"
CONVERSATION_ID = "disc-77"
EVENT_ID = "sev-77"


# --------------------------------------------------------------------------- #
# The relay
# --------------------------------------------------------------------------- #


class _FakeConversationStore:
    def __init__(self, document: dict[str, Any] | None) -> None:
        self.document = document
        self.scopes: list[Any] = []
        self.fail_next_cas = 0

    async def read(self, conversation_id: str, *, scope: Any) -> dict[str, Any] | None:
        del conversation_id
        self.scopes.append(scope)
        if self.document is None:
            return None
        import copy

        return copy.deepcopy(self.document)

    async def compare_and_set(
        self,
        *,
        conversation_id: str,
        expected_version: int,
        replacement: dict[str, Any],
        scope: Any,
    ) -> bool:
        del conversation_id, scope
        if self.fail_next_cas > 0:
            self.fail_next_cas -= 1
            return False
        assert self.document is not None
        if int(self.document.get("version", 0)) != expected_version:
            return False
        self.document = replacement
        return True


class _FakeCases:
    def __init__(self, case: dict[str, Any] | None) -> None:
        self._case = case

    async def get_case(self, case_id: str) -> dict[str, Any] | None:
        del case_id
        return self._case


def _case(**overrides: Any) -> dict[str, Any]:
    document = {
        "caseId": CASE_ID,
        "tenantId": "tenant-a",
        "principalId": "associate-1",
        "channelAConversationId": CONVERSATION_ID,
    }
    document.update(overrides)
    return document


def _conversation() -> dict[str, Any]:
    """A conversation with two real turns, so the zip in `_transcript_of` runs."""
    return {
        "_id": CONVERSATION_ID,
        "conversationId": CONVERSATION_ID,
        "version": 3,
        "graphGenerationId": "gen-1",
        "turns": {
            "k1": {
                "digest": "d1",
                "result": {
                    "conversationId": CONVERSATION_ID,
                    "conversationVersion": 1,
                    "response": "Which order is this about?",
                    "state": "AWAITING_INPUT",
                },
            }
        },
        "state": {"transcript": [{"role": "associate", "text": "I need a return"}]},
    }


def _relay(store: _FakeConversationStore, cases: _FakeCases) -> SupportTranscriptRelay:
    return SupportTranscriptRelay(store=store, cases=cases, scope_factory=ConversationScope)


async def _append(relay: SupportTranscriptRelay, *, record: str | None = "RMA-1") -> bool:
    return await relay.append_system_entry(
        case_id=CASE_ID,
        support_event_id=EVENT_ID,
        entry_kind=SUPPORT_UPDATE_ENTRY_KIND,
        return_record_id=record,
        payload={"intent": "rma_issued"},
    )


@pytest.mark.asyncio
async def test_a_system_entry_lands_beside_the_turns_and_never_inside_them() -> None:
    """The turn-based contract must be byte-identical afterwards.

    `_transcript_of` is the reader the Order Discovery history endpoint uses and
    it zips the transcript against `turns` positionally. An entry appended into
    that list would either read as something the associate said or break the
    zip. Asserted by rebuilding the transcript before and after and comparing.
    """
    store = _FakeConversationStore(_conversation())
    before = _transcript_of(dict(store.document or {}))

    assert await _append(_relay(store, _FakeCases(_case()))) is True

    document = store.document or {}
    assert _transcript_of(dict(document)) == before
    assert document["turns"] == _conversation()["turns"]
    entries = document["state"][SYSTEM_ENTRIES_KEY]
    assert len(entries) == 1
    assert entries[0]["kind"] == SUPPORT_UPDATE_ENTRY_KIND
    assert entries[0]["returnRecordId"] == "RMA-1"
    assert entries[0]["payload"]["intent"] == "rma_issued"


@pytest.mark.asyncio
async def test_the_same_entry_is_appended_once_however_often_it_is_delivered() -> None:
    store = _FakeConversationStore(_conversation())
    relay = _relay(store, _FakeCases(_case()))
    assert await _append(relay) is True
    assert await _append(relay) is False
    assert await _append(relay) is False
    assert len((store.document or {})["state"][SYSTEM_ENTRIES_KEY]) == 1


@pytest.mark.asyncio
async def test_a_fan_out_writes_one_entry_per_record() -> None:
    store = _FakeConversationStore(_conversation())
    relay = _relay(store, _FakeCases(_case()))
    assert await _append(relay, record="RMA-1") is True
    assert await _append(relay, record="RMA-2") is True
    entries = (store.document or {})["state"][SYSTEM_ENTRIES_KEY]
    assert [entry["returnRecordId"] for entry in entries] == ["RMA-1", "RMA-2"]
    assert len({entry["entryId"] for entry in entries}) == 2


def test_the_entry_id_cannot_collide_across_a_shifted_boundary() -> None:
    """Length-prefixed, so a part that contains the separator cannot forge one.

    This test has been wrong twice, in two different ways, and the docstring is
    the record of what it actually pins now.

    The parts are joined with `|`. A collision therefore needs **both** things:
    the shifted boundary must be between *adjacent* parts (a shift across a
    fixed part in the middle can never collapse), and one of those parts must
    be able to contain the separator itself. Adjacency alone is not enough --
    `"a|bc|K|r"` and `"ab|c|K|r"` differ under a plain join, so a test built
    from separator-free inputs passes with the length prefixes deleted, which
    is exactly what the previous two versions of this test did.

    The inputs below carry a `|` inside a part. Under the shipped
    length-prefixed encoding they stay distinct (`1:a|3:b|c|...` against
    `3:a|b|1:c|...`); under a bare `"|".join` they both render `a|b|c|K|r` and
    the two identities become one.
    """
    one = system_entry_id(case_id="a", support_event_id="b|c", entry_kind="K", return_record_id="r")
    two = system_entry_id(case_id="a|b", support_event_id="c", entry_kind="K", return_record_id="r")
    assert one != two, (
        "a part containing the separator forged a boundary: the length prefixes "
        "are what stop it, and this is the only input shape that shows it"
    )


@pytest.mark.asyncio
async def test_the_scope_comes_from_the_case_document_not_from_a_caller() -> None:
    """Tenant isolation is a query filter, and this is where it is built."""
    store = _FakeConversationStore(_conversation())
    await _append(_relay(store, _FakeCases(_case(tenantId="tenant-z"))))
    assert store.scopes[0].filter() == {
        "tenantId": "tenant-z",
        "principalId": "associate-1",
    }


@pytest.mark.asyncio
async def test_a_case_with_no_channel_a_conversation_is_not_an_error() -> None:
    """Nowhere to relay to is not a failed analysis.

    Raising here would dead-letter a classify command whose analysis committed
    perfectly well, on the grounds that the case has no conversation -- which is
    a property of how the case was raised, not of the message.
    """
    store = _FakeConversationStore(_conversation())
    assert await _append(_relay(store, _FakeCases(_case(channelAConversationId=None)))) is False
    assert await _append(_relay(store, _FakeCases(None))) is False


@pytest.mark.asyncio
async def test_one_lost_race_is_retried_and_a_second_is_left_to_the_command() -> None:
    store = _FakeConversationStore(_conversation())
    store.fail_next_cas = 1
    assert await _append(_relay(store, _FakeCases(_case()))) is True

    store = _FakeConversationStore(_conversation())
    store.fail_next_cas = 2
    assert await _append(_relay(store, _FakeCases(_case()))) is False, (
        "a second conflict means real contention; spinning would hold a worker "
        "against a conversation somebody is typing into"
    )


# --------------------------------------------------------------------------- #
# The envelope and the stage adapter
# --------------------------------------------------------------------------- #


def test_an_unparseable_explanation_is_an_empty_answer_not_a_crash() -> None:
    """An unusable answer is an attempt that failed, not an unreachable route."""
    envelope = SupportAnalysisEnvelope(
        decision="REVIEW_REQUIRED", explanation="not json at all", confidenceMillionths=0
    )
    assert envelope.parsed_explanation() == {}
    listy = SupportAnalysisEnvelope(
        decision="REVIEW_REQUIRED", explanation="[1, 2]", confidenceMillionths=0
    )
    assert listy.parsed_explanation() == {}


class _FakeTask:
    promptVersion = "support-message-classify-v1"
    allowedProviders = ("GOOGLE", "NVIDIA", "ANTHROPIC")

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        del mode
        return {
            "tier": "STANDARD",
            "promptVersion": self.promptVersion,
            "allowedProviders": list(self.allowedProviders),
            "allowTierEscalation": False,
            "fallbackStrategy": "SEQUENTIAL",
            "fallbackTemplate": "support-unavailable",
            "maximumInputTokens": 4000,
        }


class _FakeGatewayConfiguration:
    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        del mode
        return {
            "schemaVersion": "1.0",
            "circuitBreaker": {},
            "retry": {},
            "rateLimits": {},
            "providerLimits": {},
            "modelContexts": [],
        }


class _FakeDispatcher:
    configuration = _FakeGatewayConfiguration()


class _FakeInvocation:
    def __init__(self, value: SupportAnalysisEnvelope, provider: str) -> None:
        self.value = value
        self.provider = provider


class _FakeStructuredInvoker:
    task = _FakeTask()
    dispatcher = _FakeDispatcher()

    def __init__(self, *, unavailable: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._unavailable = unavailable

    async def invoke(self, *, payload: Any, size_probe: str, log_context: Any) -> Any:
        self.calls.append({"payload": dict(payload), "probe": size_probe, "log": dict(log_context)})
        if self._unavailable:
            from return_platform.ai.gateway.structured_invocation import (
                StructuredInvocationUnavailable,
            )

            raise StructuredInvocationUnavailable("every candidate route failed")
        return _FakeInvocation(
            SupportAnalysisEnvelope(
                decision="REVIEW_REQUIRED",
                explanation='{"intent": "rma_issued"}',
                confidenceMillionths=900_000,
            ),
            provider="NVIDIA",
        )


def test_the_pinned_candidates_are_the_releases_providers_in_declaration_order() -> None:
    """Declaration order is the operator's ranking; sorting would replace it."""
    adapter = StructuredStageInvoker(_FakeStructuredInvoker())
    assert adapter.ordered_candidate_routes == ("GOOGLE", "NVIDIA", "ANTHROPIC")
    assert adapter.release_id == "support-message-classify-v1"
    # Derived from the released document, not supplied by whoever built this.
    assert adapter.routing_policy_version.startswith("1.0:")


@pytest.mark.asyncio
async def test_the_adapter_records_who_answered_beside_the_pinned_candidate() -> None:
    invoker = _FakeStructuredInvoker()
    adapter = StructuredStageInvoker(invoker)
    result = await adapter.invoke(route_id="GOOGLE", payload={"bodyText": "hello"})

    assert result["intent"] == "rma_issued"
    assert result["provider"] == "NVIDIA", (
        "the pin says who was eligible; this says who replied, and the audit trail needs both"
    )
    assert invoker.calls[0]["probe"] == "hello"
    assert invoker.calls[0]["log"]["routeCandidate"] == "GOOGLE"


@pytest.mark.asyncio
async def test_an_unavailable_invocation_becomes_the_records_route_failure() -> None:
    adapter = StructuredStageInvoker(_FakeStructuredInvoker(unavailable=True))
    with pytest.raises(RouteUnavailableError):
        await adapter.invoke(route_id="GOOGLE", payload={"bodyText": "hello"})


# --------------------------------------------------------------------------- #
# The topic dispatcher
# --------------------------------------------------------------------------- #


class _FakeIngress:
    def __init__(self, stored: dict[str, Any] | None) -> None:
        self._stored = stored

    async def get_inbound(self, *, support_event_id: str) -> dict[str, Any] | None:
        del support_event_id
        return self._stored


class _FakeAnalyser:
    def __init__(self, error: BaseException | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._error = error

    async def analyse(self, **kwargs: Any) -> AnalysisOutcome:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return AnalysisOutcome(
            support_event_id=kwargs["support_event_id"],
            intent="rma_issued",
            reused_classification=False,
            reused_extraction=False,
        )


def _command() -> OutboxCommand:
    return OutboxCommand(
        id="cmd-1",
        topic="return-case.support-message.classify",
        aggregate_type="RETURN_CASE",
        aggregate_id=CASE_ID,
        idempotency_key="k",
        payload={
            "caseId": CASE_ID,
            "workflowId": "return-case-77",
            "supportEventId": EVENT_ID,
        },
        attempt_count=1,
    )


def _stored() -> dict[str, Any]:
    return {
        "caseId": CASE_ID,
        "workItemId": "wi-77",
        "rawBody": "RMA-1 is issued.",
        "correlationId": "corr-1",
    }


@pytest.mark.asyncio
async def test_the_dispatcher_hands_the_analyser_the_stored_message() -> None:
    analyser = _FakeAnalyser()
    dispatcher = SupportMessageClassifyDispatcher(
        analyser=analyser, ingress=_FakeIngress(_stored())
    )
    result = await dispatcher.dispatch(_command())

    assert result.external_reference == EVENT_ID
    call = analyser.calls[0]
    assert call["body_text"] == "RMA-1 is issued."
    assert call["work_item_id"] == "wi-77"
    assert call["correlation_id"] == "corr-1"


@pytest.mark.asyncio
async def test_a_command_naming_no_stored_message_dead_letters() -> None:
    """Retrying cannot make an event exist."""
    dispatcher = SupportMessageClassifyDispatcher(
        analyser=_FakeAnalyser(), ingress=_FakeIngress(None)
    )
    with pytest.raises(PermanentDeliveryFailure) as raised:
        await dispatcher.dispatch(_command())
    assert raised.value.error_code == UNKNOWN_SUPPORT_EVENT


@pytest.mark.asyncio
async def test_a_blocked_analysis_dead_letters_rather_than_retrying_forever() -> None:
    analyser = _FakeAnalyser(
        CandidateRoutesExhaustedError(EVENT_ID, AnalysisStage.CLASSIFICATION, ("a", "b"))
    )
    dispatcher = SupportMessageClassifyDispatcher(
        analyser=analyser, ingress=_FakeIngress(_stored())
    )
    with pytest.raises(PermanentDeliveryFailure) as raised:
        await dispatcher.dispatch(_command())
    assert raised.value.error_code == ANALYSIS_BLOCKED


@pytest.mark.asyncio
async def test_an_infrastructure_blip_is_retried_and_a_bug_is_not() -> None:
    """The pair that must not be collapsed.

    A connection error is an outage and retries. A `ValueError` from the
    analyser is a bug, and retrying it forever would hide it behind a queue
    depth graph -- so it propagates as itself and the outbox's own failure
    handling decides.
    """
    blip = SupportMessageClassifyDispatcher(
        analyser=_FakeAnalyser(ConnectionError("provider unreachable")),
        ingress=_FakeIngress(_stored()),
    )
    with pytest.raises(TransientDeliveryFailure):
        await blip.dispatch(_command())

    bug = SupportMessageClassifyDispatcher(
        analyser=_FakeAnalyser(ValueError("a genuine defect")),
        ingress=_FakeIngress(_stored()),
    )
    with pytest.raises(ValueError, match="genuine defect"):
        await bug.dispatch(_command())

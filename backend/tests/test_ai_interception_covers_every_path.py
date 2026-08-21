"""AI-01: interception governs every path, not the one it was written in.

Interception was real, audited, operator-visible — and it was an `if` in the
middle of `AIGatewayService.evaluate`'s provider loop. The Order Agent and the
Graph Analyzer reach a provider through `StructuredOutputInvoker`, which had its
own loop, so turning interception on stopped eligibility decisions and did
nothing whatever to the reasoning traffic that carries the most customer data.
The analyzer's prompt block 5 is UNTRUSTED SOURCE SAMPLE — rows read out of a
customer's database — and it was the least gated call on the platform.

Nothing was misconfigured. The control was attached to a loop instead of to a
boundary, and there were three loops.

The load-bearing test here is `test_adversarial_scenario_32...`, stated verbatim
in the audit and in release gate RG-04, driven through the real structured
invocation path because bypassing that path *is* the defect. It asserts the
provider invocation COUNT, not that a flag was read: a gate that is consulted and
then not obeyed reads identically to one that works.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from return_platform.ai.gateway.final_dispatch import (
    ALLOW_ALL,
    DispatchDecision,
    DispatchRequest,
    FinalDispatcher,
    InterceptionPolicy,
)
from return_platform.ai.gateway.interception_policy import (
    DurableInterceptionPolicy,
    build_interception_policy,
    interception_id_for,
)
from return_platform.ai.gateway.redaction import REDACTED
from return_platform.ai.gateway.structured_invocation import (
    StructuredInvocationUnavailable,
    StructuredOutputInvoker,
)
from return_platform.ai.gateway.telemetry import InvocationCorrelation
from return_platform.ai.interception.records import (
    RESUMABLE,
    Interception,
    InterceptionStatus,
    ResumeCommand,
)
from return_platform.ai.interception.store import InterceptionNotPending
from return_platform.ai.providers import ProviderRequest, ProviderResponse
from return_platform.ai.routing.routes import AIRoute
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import ModelTier, load_ai_gateway_configuration
from return_platform.configuration.settings import Settings

CONFIG = Path(__file__).resolve().parents[1] / "config" / "ai_gateway.yaml"
_STRUCTURED_TEXT = '{"verdict":"ok"}'


class _Answer(BaseModel):
    verdict: str


class _CountingProvider:
    """The instrument the proof actually turns on: a call counter.

    Asserting that a policy was *consulted* would pass for a gate that is read
    and then ignored, which is a plausible way to reimplement this defect.
    """

    configured = True
    name = "GOOGLE"
    model = "model-standard"

    def __init__(self) -> None:
        self.calls: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        return ProviderResponse(
            provider=self.name,
            model=self.model,
            text=_STRUCTURED_TEXT,
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
        )


class _MemoryInterceptionStore:
    """The real store's contract, in memory.

    Not a mock of the policy: the policy's whole job is the state machine over
    these transitions, so substituting it would test the substitute.
    `SystemStoreInterceptionStore` itself is exercised against real Mongo in
    `test_durable_interception_real_infra.py`.
    """

    def __init__(self) -> None:
        self.records: dict[str, Interception] = {}
        self.payloads: dict[str, dict[str, Any]] = {}

    async def open(
        self,
        *,
        interception_id: str,
        task_id: str,
        request_payload: Any,
        resume: ResumeCommand,
        expires_at: datetime,
    ) -> Interception:
        record = Interception(
            interception_id=interception_id,
            task_id=task_id,
            status=InterceptionStatus.PENDING,
            resume=resume,
            created_at=datetime.now(UTC),
            expires_at=expires_at,
        )
        self.records[interception_id] = record
        self.payloads[interception_id] = dict(request_payload)
        return record

    async def get(self, interception_id: str) -> Interception | None:
        return self.records.get(interception_id)

    async def request_payload(self, interception_id: str) -> dict[str, Any] | None:
        return self.payloads.get(interception_id)

    async def answer(
        self, *, interception_id: str, response_text: str, answered_by: str
    ) -> Interception:
        record = self._require_pending(interception_id)
        self.payloads[interception_id]["responseText"] = response_text
        updated = Interception(
            interception_id=record.interception_id,
            task_id=record.task_id,
            status=InterceptionStatus.ANSWERED,
            resume=record.resume,
            created_at=record.created_at,
            expires_at=record.expires_at,
            answered_at=datetime.now(UTC),
            answered_by=answered_by,
            response_text=response_text,
        )
        self.records[interception_id] = updated
        return updated

    async def allow(self, *, interception_id: str, allowed_by: str) -> Interception:
        record = self._require_pending(interception_id)
        updated = Interception(
            interception_id=record.interception_id,
            task_id=record.task_id,
            status=InterceptionStatus.ALLOWED,
            resume=record.resume,
            created_at=record.created_at,
            expires_at=record.expires_at,
            answered_at=datetime.now(UTC),
            answered_by=allowed_by,
        )
        self.records[interception_id] = updated
        return updated

    async def cancel(self, *, interception_id: str, status: InterceptionStatus) -> None:
        record = self.records.get(interception_id)
        if record is None or record.status is not InterceptionStatus.PENDING:
            return
        self.records[interception_id] = Interception(
            interception_id=record.interception_id,
            task_id=record.task_id,
            status=status,
            resume=record.resume,
            created_at=record.created_at,
            expires_at=record.expires_at,
        )

    async def list_pending(self, *, limit: int = 100) -> list[Interception]:
        return [
            record
            for record in self.records.values()
            if record.status is InterceptionStatus.PENDING
        ][:limit]

    def _require_pending(self, interception_id: str) -> Interception:
        record = self.records.get(interception_id)
        if record is None:
            raise InterceptionNotPending(f"interception {interception_id!r} does not exist")
        if record.status is not InterceptionStatus.PENDING:
            raise InterceptionNotPending(
                f"interception {interception_id!r} is {record.status.value}, not PENDING"
            )
        return record


class _Switch:
    """The operator's global `interceptMode`, as the real settings surface."""

    def __init__(self, on: bool) -> None:
        self.on = on

    async def get_ai_settings(self) -> Any:
        class _View:
            interceptMode = self.on

        return _View()


def _settings() -> Settings:
    return Settings.model_construct(
        environment="test",
        ai_gateway_configuration_path=CONFIG,
        ai_timeout_seconds=2.0,
        ai_global_timeout_seconds=10.0,
        ai_max_payload_bytes=8_192,
        ai_provider_order="GOOGLE,NVIDIA,SIMULATOR",
        ai_requests_per_minute=120,
    )


def _route(provider: Any) -> AIRoute:
    return AIRoute(
        route_id=f"google/{provider.model}/key-1",
        provider_name="GOOGLE",
        model=provider.model,
        credential_id="key-1",
        credential_fingerprint="test",
        tier=ModelTier.STANDARD,
        provider=provider,
        provider_priority=0,
        model_priority=0,
        credential_priority=0,
    )


def _structured_task_id(configuration: Any) -> str:
    for task_id, task in sorted(configuration.tasks.items()):
        if task.tier is ModelTier.STANDARD and "SIMULATOR" not in task.allowedProviders:
            return task_id
    pytest.skip("no STANDARD non-simulator task is configured")


def _harness(
    *, intercept: bool
) -> tuple[StructuredOutputInvoker[_Answer], _CountingProvider, _MemoryInterceptionStore]:
    loaded = load_ai_gateway_configuration(CONFIG)
    provider = _CountingProvider()
    pool = AIRoutePool((_route(provider),), loaded.configuration)
    store = _MemoryInterceptionStore()
    invoker: StructuredOutputInvoker[_Answer] = StructuredOutputInvoker(
        settings=_settings(),
        configuration=loaded.configuration,
        route_pool=pool,
        task_id=_structured_task_id(loaded.configuration),
        response_model=_Answer,
        logger=logging.getLogger("test"),
        event_prefix="test",
        subject="test invocation",
        interception=build_interception_policy(
            store=store,
            settings_source=_Switch(intercept),
            subject="test",
        ),
    )
    return invoker, provider, store


_PAYLOAD: dict[str, Any] = {"contextJson": '{"orderId":"SO-9"}'}


# --------------------------------------------------------------------------
# The mandatory closure proof
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adversarial_scenario_32_structured_call_is_held_then_allowed() -> None:
    """RG-04, verbatim:

        interception ON
        → structured Order Agent call
        → provider invocation count = 0
        → explicit ALLOW_PROVIDER
        → provider invocation count = 1

    Driven through `StructuredOutputInvoker.invoke` — the real path the Order
    Agent and the Graph Analyzer use — because bypassing that path is the whole
    defect. Before AI-01 the first assertion below read 1.
    """
    invoker, provider, store = _harness(intercept=True)

    # interception ON → structured call → held.
    with pytest.raises(StructuredInvocationUnavailable, match="HUMAN_RESPONSE"):
        await invoker.invoke(payload=_PAYLOAD, size_probe="small", log_context={})

    assert len(provider.calls) == 0, "a held request reached the provider"

    pending = await store.list_pending()
    assert len(pending) == 1, "interception ON must persist exactly one held request"

    # explicit ALLOW_PROVIDER — the operator action, through the store the
    # `/interceptions/{id}/allow` endpoint drives.
    allowed = await store.allow(interception_id=pending[0].interception_id, allowed_by="operator-1")
    assert allowed.status is InterceptionStatus.ALLOWED

    # The resumed call. Same payload, therefore the same derived id, therefore
    # the same interception — which is now approved.
    result = await invoker.invoke(payload=_PAYLOAD, size_probe="small", log_context={})

    assert result.value.verdict == "ok"
    assert len(provider.calls) == 1, (
        f"expected exactly one provider call after ALLOW_PROVIDER, got {len(provider.calls)}"
    )


# --------------------------------------------------------------------------
# The other two outcomes, and the states around them
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_human_answer_is_returned_as_manual_and_never_reaches_a_provider() -> None:
    """`HUMAN_RESPONSE`. The answer is reported as MANUAL rather than as the
    model it replaced — an evaluation set built from these records would
    otherwise be measuring human text as though a model produced it."""
    invoker, provider, store = _harness(intercept=True)

    with pytest.raises(StructuredInvocationUnavailable):
        await invoker.invoke(payload=_PAYLOAD, size_probe="small", log_context={})
    pending = await store.list_pending()
    await store.answer(
        interception_id=pending[0].interception_id,
        response_text='{"verdict":"human"}',
        answered_by="operator-1",
    )

    # The resumed call, which is what this test is named for. The operator's
    # answer comes back as a parsed result -- it went through `inspect_output`
    # and this caller's own `validate` inside the dispatcher, so it cleared the
    # same bar a provider's reply clears and is not a fabrication.
    #
    # This assertion used to be `pytest.raises(..., match="HUMAN_RESPONSE")`,
    # which made the whole feature write-only: a request could be held and
    # answered, and the answer was then discarded on the way back to the caller.
    # Nothing could resume, and the test read as though that were intended.
    result = await invoker.invoke(payload=_PAYLOAD, size_probe="small", log_context={})

    assert result.value.verdict == "human"
    # The point the docstring makes: reported as MANUAL, never as the model it
    # replaced, and with no token counts to inflate a model's usage.
    assert result.provider == "MANUAL"
    assert result.model == "manual-human-v1"
    assert result.prompt_tokens == 0
    assert result.completion_tokens == 0
    assert len(provider.calls) == 0


@pytest.mark.asyncio
async def test_a_cancelled_interception_is_a_reject_and_stays_one() -> None:
    """`REJECT`. A cancelled request must not quietly dispatch on the next
    attempt — a retry loop would otherwise defeat the operator's decision by
    simply asking again."""
    invoker, provider, store = _harness(intercept=True)

    with pytest.raises(StructuredInvocationUnavailable):
        await invoker.invoke(payload=_PAYLOAD, size_probe="small", log_context={})
    pending = await store.list_pending()
    await store.cancel(
        interception_id=pending[0].interception_id, status=InterceptionStatus.CANCELLED
    )

    for _ in range(3):
        with pytest.raises(StructuredInvocationUnavailable, match="REJECT"):
            await invoker.invoke(payload=_PAYLOAD, size_probe="small", log_context={})
    assert len(provider.calls) == 0


@pytest.mark.asyncio
async def test_a_decision_outlives_the_switch_being_turned_off() -> None:
    """A run held while interception was on must not resume by bypassing the
    decision an operator already made. Re-checking the switch first would let
    'turn it off' silently approve everything that was waiting — including the
    requests an operator had deliberately cancelled."""
    loaded = load_ai_gateway_configuration(CONFIG)
    provider = _CountingProvider()
    pool = AIRoutePool((_route(provider),), loaded.configuration)
    store = _MemoryInterceptionStore()
    switch = _Switch(True)
    invoker: StructuredOutputInvoker[_Answer] = StructuredOutputInvoker(
        settings=_settings(),
        configuration=loaded.configuration,
        route_pool=pool,
        task_id=_structured_task_id(loaded.configuration),
        response_model=_Answer,
        logger=logging.getLogger("test"),
        event_prefix="test",
        subject="test invocation",
        interception=build_interception_policy(store=store, settings_source=switch, subject="test"),
    )

    with pytest.raises(StructuredInvocationUnavailable):
        await invoker.invoke(payload=_PAYLOAD, size_probe="small", log_context={})
    pending = await store.list_pending()
    await store.cancel(
        interception_id=pending[0].interception_id, status=InterceptionStatus.CANCELLED
    )

    switch.on = False
    with pytest.raises(StructuredInvocationUnavailable, match="REJECT"):
        await invoker.invoke(payload=_PAYLOAD, size_probe="small", log_context={})
    assert len(provider.calls) == 0


@pytest.mark.asyncio
async def test_interception_off_dispatches_without_persisting_anything() -> None:
    """The switch still means what it meant. A gate that held requests when it
    was off would be a worse regression than the one being fixed."""
    invoker, provider, store = _harness(intercept=False)

    result = await invoker.invoke(payload=_PAYLOAD, size_probe="small", log_context={})

    assert result.value.verdict == "ok"
    assert len(provider.calls) == 1
    assert store.records == {}, "interception OFF must not persist a held request"


# --------------------------------------------------------------------------
# Identity, redaction, and the structural guarantees
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_retried_turn_reuses_its_interception_rather_than_opening_a_second() -> None:
    """The id is derived, not generated. A random id would make the resumed call
    open a second interception and wait for an operator to answer the same
    question twice — forever, since each retry opens another."""
    invoker, _, store = _harness(intercept=True)

    for _ in range(3):
        with pytest.raises(StructuredInvocationUnavailable):
            await invoker.invoke(payload=_PAYLOAD, size_probe="small", log_context={})

    assert len(store.records) == 1, f"one turn opened {len(store.records)} interceptions"


@pytest.mark.asyncio
async def test_different_turns_do_not_share_one_interception() -> None:
    """The other half of the identity claim: approving one request must not
    approve every future one that happens to share a task."""
    invoker, _, store = _harness(intercept=True)

    for payload in ({"contextJson": '{"orderId":"SO-1"}'}, {"contextJson": '{"orderId":"SO-2"}'}):
        with pytest.raises(StructuredInvocationUnavailable):
            await invoker.invoke(payload=payload, size_probe="small", log_context={})

    assert len(store.records) == 2


@pytest.mark.asyncio
async def test_nothing_unredacted_is_persisted_by_interception() -> None:
    """The sequencing requirement behind moving redaction to the top of
    `dispatch`.

    Interception persists the held request so an operator can read it. If
    redaction ran after the interception decision — where it used to, at
    `ProviderRequest` construction — the held-request store would hold customer
    data the provider itself never received. Proven once here because there is
    one boundary; before AI-02 it would have needed proving per path.
    """
    invoker, provider, store = _harness(intercept=True)

    with pytest.raises(StructuredInvocationUnavailable):
        await invoker.invoke(
            payload={"contextJson": '{"customer_email":"jane@example.test","orderId":"SO-9"}'},
            size_probe="small",
            log_context={},
        )

    assert provider.calls == []
    sealed = str(next(iter(store.payloads.values())))
    assert "jane@example.test" not in sealed, "interception persisted an unredacted payload"
    assert REDACTED in sealed
    assert "SO-9" in sealed, "redaction must mask leaves, not blank the document"


def test_the_boundary_has_no_default_interception_policy() -> None:
    """AI-01 was not a missing mechanism. It was a mechanism two of three callers
    never opted into, and `interception: InterceptionPolicy = ALLOW_ALL` is what
    made not opting in the silent option. Removing the default is what makes a
    fourth caller's omission a type error instead of a security finding."""
    import inspect

    for target in (FinalDispatcher.__init__, StructuredOutputInvoker.__init__):
        parameter = inspect.signature(target).parameters["interception"]
        assert parameter.default is inspect.Parameter.empty, (
            f"{target.__qualname__} still defaults its interception policy"
        )


def test_an_ungated_process_says_so(caplog: pytest.LogCaptureFixture) -> None:
    """A deployment with no interception store genuinely cannot hold a request,
    and failing startup over it would take the platform down to protect a feature
    that deployment does not use. But an operator who enables interception and
    watches reasoning traffic sail past deserves the line naming the path that
    could not be gated — which is the diagnosis nobody had for AI-01."""
    with caplog.at_level(logging.WARNING):
        policy = build_interception_policy(
            store=None, settings_source=_Switch(True), subject="order_agent_reasoning"
        )

    assert policy is ALLOW_ALL
    assert any("ai_interception_ungated" in message for message in caplog.messages)


def test_every_decided_interception_is_resumable_not_only_answered() -> None:
    """`ALLOWED` must reach the resume dispatcher. Enqueueing only `ANSWERED`
    would make "allow this through" a decision that is recorded and then never
    acted on, which is worse than not offering the action at all."""
    assert RESUMABLE == {InterceptionStatus.ANSWERED, InterceptionStatus.ALLOWED}


@pytest.mark.asyncio
async def test_the_three_outcomes_are_the_only_ones_the_policy_can_produce() -> None:
    """C7 permits exactly `ALLOW_PROVIDER | HUMAN_RESPONSE | REJECT`. Five stored
    states map onto three decisions; a fourth would put a state outside the
    contract into every consumer of `DispatchOutcome`."""
    store = _MemoryInterceptionStore()
    policy = DurableInterceptionPolicy(store=store, enabled=_Switch(True).get_ai_settings)
    loaded = load_ai_gateway_configuration(CONFIG)
    task_id = _structured_task_id(loaded.configuration)
    request = DispatchRequest(
        task_id=task_id,
        task=loaded.configuration.tasks[task_id],
        system_prompt="p",
        payload={},
        request_digest="d",
        estimated_tokens=1,
    )
    interception_id = interception_id_for(request)

    seen = {(await policy.decide(request)).decision}
    await store.allow(interception_id=interception_id, allowed_by="op")
    seen.add((await policy.decide(request)).decision)

    store.records[interception_id] = Interception(
        interception_id=interception_id,
        task_id=task_id,
        status=InterceptionStatus.EXPIRED,
        resume=ResumeCommand(run_id="r", thread_id="t"),
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(seconds=1),
    )
    seen.add((await policy.decide(request)).decision)

    assert seen == {
        DispatchDecision.HUMAN_RESPONSE,
        DispatchDecision.ALLOW_PROVIDER,
        DispatchDecision.REJECT,
    }
    assert set(DispatchDecision) == seen


def _dispatch_request(**correlation: str) -> DispatchRequest:
    loaded = load_ai_gateway_configuration(CONFIG)
    task_id = _structured_task_id(loaded.configuration)
    return DispatchRequest(
        task_id=task_id,
        task=loaded.configuration.tasks[task_id],
        system_prompt="p",
        payload={},
        request_digest="d",
        estimated_tokens=1,
        correlation=InvocationCorrelation(**correlation),
    )


def test_a_retried_turn_derives_the_same_interception_id() -> None:
    """The invariant `interception_id_for` documents, now actually held.

    `correlation_id` is minted per HTTP request by the API middleware, so two
    attempts at one order-agent turn carry different values. Keying on it meant
    an operator answered a held request and the retry -- looking for that answer
    -- derived a different id, opened a second interception, and asked the same
    question again. Manual mode could hold a turn and never resume one.
    """
    first = _dispatch_request(turn_id="turn-1", correlation_id="http-request-1")
    retry = _dispatch_request(turn_id="turn-1", correlation_id="http-request-2")

    assert interception_id_for(first) == interception_id_for(retry)


def test_two_turns_of_one_conversation_stay_distinct() -> None:
    """The other half of the same invariant. Collapsing the two would make one
    operator answer serve a question they were never shown."""
    conversation = {"conversation_id": "conv-1", "correlation_id": "http-request-1"}
    first = _dispatch_request(turn_id="turn-1", **conversation)
    second = _dispatch_request(turn_id="turn-2", **conversation)

    assert interception_id_for(first) != interception_id_for(second)


def test_a_caller_without_a_turn_identity_still_keys_on_its_request() -> None:
    """`turn_id` is optional, and its absence must change nothing. Every caller
    that is not a conversational turn -- schema analysis, eligibility, the
    simulator -- has no turn to be retried as, and the HTTP request remains the
    right unit for them."""
    first = _dispatch_request(correlation_id="http-request-1")
    second = _dispatch_request(correlation_id="http-request-2")

    assert interception_id_for(first) != interception_id_for(second)
    assert interception_id_for(first) == interception_id_for(
        _dispatch_request(correlation_id="http-request-1")
    )


def test_the_policy_satisfies_the_boundary_protocol() -> None:
    store = _MemoryInterceptionStore()
    policy = DurableInterceptionPolicy(store=store, enabled=_Switch(False).get_ai_settings)
    assert isinstance(policy, InterceptionPolicy)

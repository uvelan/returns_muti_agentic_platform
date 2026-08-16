"""The second interception point: holding what the model said.

The first point holds a *request* -- the provider is never called and a human
answers in its place. That is the only control the platform had, and it is the
wrong shape for the thing operators actually want, which is to read what the
model produced before anything acts on it. This adds the second point: after the
provider answers and before the reply is inspected, parsed or returned, a human
may accept it, edit it, or reject it.

THE LOAD-BEARING TEST HERE IS PROVENANCE
----------------------------------------
`test_an_edited_response_is_neither_the_model_nor_manual` is the one the design
turns on. This codebase is deliberate that human output stays labelled human --
`durable_interception.py` says merging the two "would put a person's words into
an evaluation set as though a model had written them" -- and an *edited*
response is the case neither existing label fits. It is a model response a
human changed. Reported as the model, an evaluation set absorbs human edits as
model quality; reported as MANUAL, the fact that a model produced the substance
is lost.

So it gets a third identity, `HUMAN_EDITED` / `human-edited-v1`, carrying the
originating provider and model, who edited it, and the digest of the text before
the edit alongside the digest of the text delivered. The tests below assert both
halves of that -- that the origin survives, and that the edit is not silently
readable as the origin.

The other rule this file exists to hold down: **an edit is not trusted more than
the model's own answer.** It goes through the identical `inspect_output` and the
identical schema parse, and `test_a_malformed_edit_fails_validation...` proves a
free-text edit is rejected exactly as a free-text paste is at the request point.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from return_platform.ai.gateway.final_dispatch import (
    RESPONSE_REJECTED,
    RESPONSE_REVIEW_EXPIRED,
    DispatchRequest,
    FinalDispatcher,
    InterceptionPolicy,
    ResponseDecision,
)
from return_platform.ai.gateway.interception_policy import (
    DurableInterceptionPolicy,
    human_edited_response,
    interception_id_for,
    response_interception_id_for,
)
from return_platform.ai.gateway.structured_invocation import (
    StructuredInvocationUnavailable,
    StructuredOutputInvoker,
)
from return_platform.ai.gateway.telemetry import AIAttemptRecord, InvocationCorrelation
from return_platform.ai.interception.dispatcher import InterceptionResumeDispatcher
from return_platform.ai.interception.records import (
    Interception,
    InterceptionPoint,
    InterceptionStatus,
    ResumeCommand,
)
from return_platform.ai.interception.store import InterceptionNotPending
from return_platform.ai.pricing import AICostEstimate, AIPricingStatus
from return_platform.ai.providers import (
    HUMAN_EDITED_MODEL,
    HUMAN_EDITED_PROVIDER,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
from return_platform.ai.routing.routes import AIRoute
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import ModelTier, load_ai_gateway_configuration
from return_platform.configuration.settings import Settings

CONFIG = Path(__file__).resolve().parents[1] / "config" / "ai_gateway.yaml"

_MODEL_TEXT = '{"verdict":"model"}'
_EDITED_TEXT = '{"verdict":"human-edited"}'


class _Answer(BaseModel):
    verdict: str


class _ScriptedProvider:
    """One reply per call, so a failover can be told from a retry.

    A counter alone cannot distinguish "the loop moved to the second route" from
    "the loop asked the first route twice", and that distinction is the entire
    content of the REJECTED semantics.
    """

    configured = True

    def __init__(self, name: str, model: str, text: str) -> None:
        self.name = name
        self.model = model
        self._text = text
        self.calls: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        return ProviderResponse(
            provider=self.name,
            model=self.model,
            text=self._text,
            input_tokens=11,
            output_tokens=7,
            total_tokens=18,
        )


class _MemoryInterceptionStore:
    """The real store's contract, in memory, now carrying the point.

    Not a mock of the policy: the policy's job is the state machine over these
    transitions, so substituting it would test the substitute.
    """

    def __init__(self) -> None:
        self.records: dict[str, Interception] = {}
        self.payloads: dict[str, dict[str, Any]] = {}
        self.opens: list[tuple[str, InterceptionPoint]] = []

    async def open(
        self,
        *,
        interception_id: str,
        task_id: str,
        request_payload: Any,
        resume: ResumeCommand,
        expires_at: datetime,
        point: InterceptionPoint = InterceptionPoint.REQUEST,
    ) -> Interception:
        record = Interception(
            interception_id=interception_id,
            task_id=task_id,
            status=InterceptionStatus.PENDING,
            resume=resume,
            created_at=datetime.now(UTC),
            expires_at=expires_at,
            point=point,
        )
        self.records[interception_id] = record
        self.payloads[interception_id] = dict(request_payload)
        self.opens.append((interception_id, point))
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
        return self._replace(
            record,
            status=InterceptionStatus.ANSWERED,
            answered_by=answered_by,
            response_text=response_text,
        )

    async def allow(self, *, interception_id: str, allowed_by: str) -> Interception:
        record = self._require_pending(interception_id)
        return self._replace(record, status=InterceptionStatus.ALLOWED, answered_by=allowed_by)

    async def cancel(self, *, interception_id: str, status: InterceptionStatus) -> None:
        record = self.records.get(interception_id)
        if record is None or record.status is not InterceptionStatus.PENDING:
            return
        self._replace(record, status=status, answered_by=None)

    async def list_pending(self, *, limit: int = 100) -> list[Interception]:
        return [
            record
            for record in self.records.values()
            if record.status is InterceptionStatus.PENDING
        ][:limit]

    def _replace(
        self,
        record: Interception,
        *,
        status: InterceptionStatus,
        answered_by: str | None,
        response_text: str | None = None,
    ) -> Interception:
        updated = Interception(
            interception_id=record.interception_id,
            task_id=record.task_id,
            status=status,
            resume=record.resume,
            created_at=record.created_at,
            expires_at=record.expires_at,
            answered_at=datetime.now(UTC) if answered_by else None,
            answered_by=answered_by,
            response_text=response_text,
            point=record.point,
        )
        self.records[record.interception_id] = updated
        return updated

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


class _Recorder:
    def __init__(self) -> None:
        self.records: list[AIAttemptRecord] = []

    async def record(self, record: AIAttemptRecord) -> None:
        self.records.append(record)


def _settings(*, review: bool = False, environment: str = "test") -> Settings:
    return Settings.model_construct(
        environment=environment,
        ai_gateway_configuration_path=CONFIG,
        ai_timeout_seconds=2.0,
        ai_global_timeout_seconds=10.0,
        ai_max_payload_bytes=8_192,
        ai_provider_order="GOOGLE,NVIDIA,SIMULATOR",
        ai_requests_per_minute=120,
        ai_response_interception=review,
    )


def _route(provider: Any, *, credential: str = "key-1") -> AIRoute:
    return AIRoute(
        route_id=f"google/{provider.model}/{credential}",
        provider_name=provider.name,
        model=provider.model,
        credential_id=credential,
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


def _policy(
    store: _MemoryInterceptionStore,
    *,
    review: bool,
    environment: str = "test",
    timeout_seconds: float = 5.0,
) -> DurableInterceptionPolicy:
    return DurableInterceptionPolicy(
        store=store,
        enabled=_never_intercept_requests,
        settings=_settings(review=review, environment=environment),
        # Fast enough that a test that genuinely waits on a decision still
        # finishes, slow enough that "the caller blocked" is observable.
        response_poll_seconds=0.005,
        response_timeout_seconds=timeout_seconds,
    )


async def _never_intercept_requests() -> bool:
    """The *request* point off, so every test here exercises only the second one."""
    return False


def _harness(
    *,
    review: bool,
    providers: tuple[_ScriptedProvider, ...] | None = None,
    environment: str = "test",
    timeout_seconds: float = 5.0,
    recorder: _Recorder | None = None,
) -> tuple[
    StructuredOutputInvoker[_Answer], tuple[_ScriptedProvider, ...], _MemoryInterceptionStore
]:
    loaded = load_ai_gateway_configuration(CONFIG)
    scripted = providers or (_ScriptedProvider("GOOGLE", "model-standard", _MODEL_TEXT),)
    pool = AIRoutePool(
        tuple(
            _route(provider, credential=f"key-{index}") for index, provider in enumerate(scripted)
        ),
        loaded.configuration,
    )
    store = _MemoryInterceptionStore()
    invoker: StructuredOutputInvoker[_Answer] = StructuredOutputInvoker(
        settings=_settings(review=review, environment=environment),
        configuration=loaded.configuration,
        route_pool=pool,
        task_id=_structured_task_id(loaded.configuration),
        response_model=_Answer,
        logger=logging.getLogger("test"),
        event_prefix="test",
        subject="test invocation",
        recorder=recorder,
        interception=_policy(
            store, review=review, environment=environment, timeout_seconds=timeout_seconds
        ),
    )
    return invoker, scripted, store


_PAYLOAD: dict[str, Any] = {"contextJson": '{"orderId":"SO-9"}'}


async def _first_pending(store: _MemoryInterceptionStore) -> Interception:
    """Wait for the hold to appear, rather than sleeping and hoping.

    Bounded so a regression that never opens a hold fails the test instead of
    hanging the suite.
    """
    for _ in range(400):
        pending = await store.list_pending()
        if pending:
            return pending[0]
        await asyncio.sleep(0.005)
    raise AssertionError("no interception was opened")


# --------------------------------------------------------------------------
# Off by default
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_off_by_default_nothing_is_held_and_nothing_is_written() -> None:
    """The requirement, stated as a test: a deployment with response
    interception off behaves exactly as it did before this existed.

    Asserted on the *store* rather than on the result, because a hold that
    opened and immediately resolved would produce an identical result and a
    write nobody asked for -- and a write is the part that costs latency and
    leaves a queue entry for an operator to wonder about.
    """
    invoker, providers, store = _harness(review=False)

    result = await invoker.invoke(payload=_PAYLOAD, size_probe="small", log_context={})

    assert result.value.verdict == "model"
    assert len(providers[0].calls) == 1
    assert store.records == {}, "response interception OFF must not persist anything"
    assert store.opens == []


def test_the_boundary_does_not_even_ask_when_review_is_off() -> None:
    """Off must cost nothing, not merely resolve fast.

    `FinalDispatcher` reads one attribute before awaiting anything, so a policy
    that does not review is never entered. A test on the attribute rather than
    on timing, because "no extra await" is a structural claim and a stopwatch
    would only ever measure the machine.
    """
    store = _MemoryInterceptionStore()
    assert _policy(store, review=False).reviews_responses is False
    assert _policy(store, review=True).reviews_responses is True
    # The permissive default the rest of the platform is wired with.
    assert InterceptionPolicy().reviews_responses is False


# --------------------------------------------------------------------------
# On: the provider runs, the reply is held, the caller waits
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_provider_is_called_and_the_caller_blocks_until_a_decision() -> None:
    """The shape of the second point, in one test.

    Unlike the request point the provider *does* run -- holding a response you
    never obtained is not a thing -- and the caller then waits, because the
    answer has already been paid for and releasing the turn would mean buying it
    twice.
    """
    invoker, providers, store = _harness(review=True)

    turn = asyncio.create_task(invoker.invoke(payload=_PAYLOAD, size_probe="small", log_context={}))
    held = await _first_pending(store)

    assert held.point is InterceptionPoint.RESPONSE
    assert len(providers[0].calls) == 1, "the response point must not suppress the call"
    assert not turn.done(), "the caller continued without a decision"

    # The sealed payload carries the reply *and* the question it answers: a
    # reviewer cannot judge an answer without seeing what was asked.
    sealed = store.payloads[held.interception_id]
    assert sealed["modelResponse"]["text"] == _MODEL_TEXT
    assert sealed["modelResponse"]["provider"] == "GOOGLE"
    assert "systemPrompt" in sealed

    await store.allow(interception_id=held.interception_id, allowed_by="operator-1")
    result = await turn

    assert result.value.verdict == "model"
    assert len(providers[0].calls) == 1, "a decision must not re-ask the provider"


@pytest.mark.asyncio
async def test_accepted_delivers_the_model_response_byte_identical_as_the_model() -> None:
    """`ACCEPTED`. An operator looked and substituted nothing, so the caller gets
    exactly what the provider produced and it is attributed to the provider --
    the same reading `ALLOWED` already has at the request point."""
    invoker, providers, store = _harness(review=True)
    turn = asyncio.create_task(invoker.invoke(payload=_PAYLOAD, size_probe="small", log_context={}))
    held = await _first_pending(store)
    await store.allow(interception_id=held.interception_id, allowed_by="operator-1")

    result = await turn

    assert result.provider == "GOOGLE"
    assert result.model == "model-standard"
    assert result.value.verdict == "model"
    # Byte-identical: the text the provider returned is the text that was parsed.
    assert providers[0].calls, "the provider was not called"


# --------------------------------------------------------------------------
# The provenance decision
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_edited_response_is_neither_the_model_nor_manual() -> None:
    """The decision this whole point turns on.

    A response a human edited is a third thing. Labelled with the originating
    model, an evaluation set built from traces measures a person's edits as
    model quality. Labelled `MANUAL`, the fact that a model produced the
    substance is gone -- and `MANUAL` already means something else: a human
    answering *instead of* a model that was never called.

    So: a third provider identity, with the origin carried alongside it. Both
    halves are asserted here, because either one alone is the failure.
    """
    invoker, _, store = _harness(review=True)
    turn = asyncio.create_task(invoker.invoke(payload=_PAYLOAD, size_probe="small", log_context={}))
    held = await _first_pending(store)
    await store.answer(
        interception_id=held.interception_id,
        response_text=_EDITED_TEXT,
        answered_by="operator-7",
    )

    result = await turn

    # The human's version is what the caller acted on.
    assert result.value.verdict == "human-edited"
    # It does not pass as the model...
    assert result.provider != "GOOGLE"
    # ...and it does not pass as a plain human answer either.
    assert result.provider != "MANUAL"
    assert (result.provider, result.model) == (HUMAN_EDITED_PROVIDER, HUMAN_EDITED_MODEL)


@pytest.mark.asyncio
async def test_the_edit_record_carries_the_origin_the_editor_and_what_changed() -> None:
    """ "Ideally with what changed" -- as digests, not as text.

    Two digests are enough to prove an edit altered something, and enough to
    line a telemetry row up against the sealed record that holds the actual
    before and after. Putting either text in a telemetry row would put customer
    data into a stream whose whole purpose is to be widely readable.
    """
    store = _MemoryInterceptionStore()
    policy = _policy(store, review=True)
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
    original = ProviderResponse(provider="GOOGLE", model="gemini-x", text=_MODEL_TEXT)

    review = asyncio.create_task(policy.review(request, original))
    held = await _first_pending(store)
    await store.answer(
        interception_id=held.interception_id,
        response_text=_EDITED_TEXT,
        answered_by="operator-7",
    )
    verdict = await review

    assert verdict.decision is ResponseDecision.EDITED
    assert verdict.response is not None
    edit = verdict.response.human_edit
    assert edit is not None
    assert (edit.origin_provider, edit.origin_model) == ("GOOGLE", "gemini-x")
    assert edit.edited_by == "operator-7"
    assert edit.interception_id == held.interception_id
    assert edit.origin_digest != edit.delivered_digest, "an edit that changed nothing"


def test_an_edited_attempt_row_cannot_be_read_as_pure_model_output() -> None:
    """The metrics store is where an evaluation set would actually be built, so
    it is where the misattribution would actually happen.

    `provider`/`model` on the row still name the route that was *called*,
    because that is what was called and what was billed. `responseAttribution`
    is what stops a reader treating the row as model output, and it is present
    and explicit on every row rather than implied by a null somebody has to
    remember to check.
    """
    edited = human_edited_response(
        ProviderResponse(provider="GOOGLE", model="gemini-x", text=_MODEL_TEXT),
        text=_EDITED_TEXT,
        edited_by="operator-7",
        interception_id="air-1",
    )

    def _row(response: ProviderResponse) -> dict[str, Any]:
        return AIAttemptRecord(
            trace_id="t",
            task_id="TASK",
            prompt_version="v1",
            attempt_number=1,
            status="SUCCESS",
            configured_tier="STANDARD",
            selected_tier="STANDARD",
            provider="GOOGLE",
            model="gemini-x",
            credential_id="key-1",
            route_id="r",
            selection_reason="HEALTHY_ROUTE_SELECTED",
            fallback_used=False,
            fallback_reason=None,
            safety_status="SAFE",
            latency_ms=1,
            rate_limit_wait_ms=0,
            input_tokens=1,
            cached_input_tokens=None,
            output_tokens=1,
            total_tokens=2,
            cost=AICostEstimate(
                amount_micros=None,
                currency=None,
                status=AIPricingStatus.UNKNOWN,
                pricing_version=None,
            ),
            correlation=InvocationCorrelation(),
            request_digest="d",
            response_digest="delivered",
            error_code=None,
            human_edit=response.human_edit,
        ).to_document()

    plain = _row(ProviderResponse(provider="GOOGLE", model="gemini-x", text=_MODEL_TEXT))
    assert plain["responseAttribution"] == "MODEL"
    assert plain["humanEditedBy"] is None
    assert plain["originalResponseDigest"] is None

    row = _row(edited)
    assert row["responseAttribution"] == "HUMAN_EDITED"
    assert row["humanEditedBy"] == "operator-7"
    # Both digests, so "an edit happened and it changed the text" is provable
    # from the row without the row carrying either version.
    assert row["originalResponseDigest"] != row["responseDigest"]
    assert row["humanEditInterceptionId"] == "air-1"


# --------------------------------------------------------------------------
# An edit is not trusted more than the model's answer
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_malformed_edit_fails_validation_and_never_reaches_the_caller() -> None:
    """The rule the request point already holds, applied to the second one.

    A human typing free text into a console is at least as likely to produce
    something malformed as a model is. The review sits *before* `inspect_output`
    and before the schema parse precisely so an edit faces the identical checks
    -- and with one route configured there is nothing to fail over to, so the
    caller gets its ordinary unavailability rather than unparsed text.
    """
    invoker, _, store = _harness(review=True)
    turn = asyncio.create_task(invoker.invoke(payload=_PAYLOAD, size_probe="small", log_context={}))
    held = await _first_pending(store)
    await store.answer(
        interception_id=held.interception_id,
        response_text="looks fine to me, ship it",
        answered_by="operator-7",
    )

    with pytest.raises(StructuredInvocationUnavailable, match="RESPONSE_INVALID"):
        await turn


# --------------------------------------------------------------------------
# REJECTED
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_rejected_response_falls_through_to_the_next_route() -> None:
    """`REJECTED` semantics, chosen and asserted.

    A rejection is a statement about *this* answer from *this* route, not about
    the request -- the request point already has a way to refuse a request. So
    the attempt fails, the code is terminal for its route (asking the same model
    the same question would hand the same operator the same text again), and the
    loop moves to the next candidate. The second route's different answer gets
    its own review, which is why the hold id includes the response digest.
    """
    first = _ScriptedProvider("GOOGLE", "model-standard", _MODEL_TEXT)
    second = _ScriptedProvider("GOOGLE", "model-standard-b", '{"verdict":"second"}')
    invoker, providers, store = _harness(review=True, providers=(first, second))

    turn = asyncio.create_task(invoker.invoke(payload=_PAYLOAD, size_probe="small", log_context={}))
    rejected = await _first_pending(store)
    await store.cancel(
        interception_id=rejected.interception_id, status=InterceptionStatus.CANCELLED
    )

    # The second route ran and produced a *different* answer, which is held
    # separately rather than inheriting the first one's rejection.
    second_hold = await _first_pending(store)
    assert second_hold.interception_id != rejected.interception_id
    await store.allow(interception_id=second_hold.interception_id, allowed_by="operator-1")

    result = await turn

    assert result.value.verdict == "second"
    assert len(providers[0].calls) == 1, "the rejected route was asked again"
    assert len(providers[1].calls) == 1


@pytest.mark.asyncio
async def test_rejecting_the_only_route_exhausts_rather_than_delivering_anything() -> None:
    """The honest report when nothing usable was produced.

    Not a special failure mode: the loop exhausts exactly as it does when every
    route returns unparseable JSON, and the caller raises its ordinary
    unavailability. Inventing a distinct terminal outcome here would give
    callers a fourth thing to handle for a case they already handle.
    """
    invoker, _, store = _harness(review=True)
    turn = asyncio.create_task(invoker.invoke(payload=_PAYLOAD, size_probe="small", log_context={}))
    held = await _first_pending(store)
    await store.cancel(interception_id=held.interception_id, status=InterceptionStatus.CANCELLED)

    with pytest.raises(StructuredInvocationUnavailable, match=RESPONSE_REJECTED):
        await turn


@pytest.mark.asyncio
async def test_an_unreviewed_response_expires_rather_than_waiting_forever() -> None:
    """Nobody at the console must not become an outage, and it must not be
    reported as a rejection: "we looked and said no" and "nobody looked" are
    different facts about a shift, and one wearing the other's name hides a rota
    problem inside what reads as model failure."""
    invoker, _, store = _harness(review=True, timeout_seconds=0.05)

    with pytest.raises(StructuredInvocationUnavailable, match=RESPONSE_REVIEW_EXPIRED):
        await invoker.invoke(payload=_PAYLOAD, size_probe="small", log_context={})

    # Closed on the way out: leaving it PENDING would show an operator work
    # whose caller has already gone.
    held = next(iter(store.records.values()))
    assert held.status is InterceptionStatus.EXPIRED


# --------------------------------------------------------------------------
# Production
# --------------------------------------------------------------------------


def test_production_refuses_the_setting_exactly_as_it_refuses_manual() -> None:
    """The same mechanism, not a third gate.

    `Settings.validate_relationships` already refuses `SIMULATOR` and `MANUAL`
    in the provider order in production. Response interception stops production
    traffic on somebody being at a console in exactly the way MANUAL does, so it
    is refused in exactly the same place.
    """
    with pytest.raises(ValueError, match="response interception cannot be enabled"):
        Settings(
            environment="production",
            # A production-legal provider order, so the refusal under test is the
            # one that fires rather than the pre-existing SIMULATOR rule.
            ai_provider_order="GOOGLE,NVIDIA",
            ai_response_interception=True,
        )

    # And the flag is not refused everywhere -- it is refused *in production*.
    # Without this the test above would pass against a setting that simply never
    # works.
    assert Settings(environment="test", ai_response_interception=True).ai_response_interception


@pytest.mark.asyncio
async def test_a_production_process_that_bypassed_the_validator_is_still_refused() -> None:
    """The belt behind the braces, and the second half of "the same mechanism".

    `DurableInterceptionProvider` raises `POLICY_BLOCKED` outside development
    and test rather than trusting the settings validator alone, because a
    human-in-the-loop control must be structurally impossible to deploy rather
    than merely refused at startup. `model_construct` is how a process reaches
    this state; the refusal is what it finds.
    """
    store = _MemoryInterceptionStore()
    policy = _policy(store, review=True, environment="production")
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

    with pytest.raises(ProviderError) as raised:
        await policy.review(
            request, ProviderResponse(provider="GOOGLE", model="m", text=_MODEL_TEXT)
        )

    assert raised.value.code == "POLICY_BLOCKED"
    assert store.records == {}, "a refused review must not queue work for an operator"


# --------------------------------------------------------------------------
# One queue
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_points_share_one_queue_and_are_distinguishable() -> None:
    """One store, one queue, one set of operator endpoints -- and a field that
    says which job a row is. A parallel store would have had to re-earn the
    sealing, the compare-and-set discipline, the expiry sweep and the console."""
    store = _MemoryInterceptionStore()
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

    # A held request, through the request policy.
    request_policy = DurableInterceptionPolicy(
        store=store, enabled=_always_intercept_requests, settings=_settings()
    )
    await request_policy.decide(request)

    # A held response, through the same store.
    response_policy = _policy(store, review=True)
    review = asyncio.create_task(
        response_policy.review(
            request, ProviderResponse(provider="GOOGLE", model="m", text=_MODEL_TEXT)
        )
    )
    for _ in range(400):
        if len(await store.list_pending()) == 2:
            break
        await asyncio.sleep(0.005)

    pending = await store.list_pending()
    assert {record.point for record in pending} == {
        InterceptionPoint.REQUEST,
        InterceptionPoint.RESPONSE,
    }
    assert len({record.interception_id for record in pending}) == 2, "the ids collided"

    response_hold = next(record for record in pending if record.point is InterceptionPoint.RESPONSE)
    await store.cancel(
        interception_id=response_hold.interception_id, status=InterceptionStatus.CANCELLED
    )
    await review


async def _always_intercept_requests() -> bool:
    return True


def test_the_two_points_derive_different_ids_for_the_same_request() -> None:
    """They share a primary key space, so a collision would make one hold
    overwrite the other -- and the response id also varies with the answer, which
    is what stops one rejection being applied to every route's reply."""
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

    assert interception_id_for(request) != response_interception_id_for(request, "a")
    assert response_interception_id_for(request, "a") != response_interception_id_for(request, "b")
    # Stable for the same answer: a crash mid-review re-finds the decision
    # instead of asking a second operator the same question.
    assert response_interception_id_for(request, "a") == response_interception_id_for(request, "a")


def test_a_held_response_is_never_turned_into_a_workflow_resume() -> None:
    """The resume bridge exists for held *requests*, whose runs were released.

    A held response blocks its caller inside the dispatch boundary -- nothing is
    suspended, so a `resume_reasoning` signal would arrive at a workflow that
    never paused. `$ne` rather than `$eq: REQUEST` because every record written
    before this field existed has no `point` at all.
    """
    queries: list[dict[str, Any]] = []

    class _Cursor:
        def sort(self, *_: Any) -> _Cursor:
            return self

        def limit(self, *_: Any) -> _Cursor:
            return self

        def __aiter__(self) -> _Cursor:
            return self

        async def __anext__(self) -> dict[str, Any]:
            raise StopAsyncIteration

    class _Collection:
        def find(self, query: dict[str, Any]) -> _Cursor:
            queries.append(query)
            return _Cursor()

    class _SystemStore:
        def read_only(self, _name: str) -> _Collection:
            return _Collection()

        def collection(self, _name: str) -> _Collection:
            return _Collection()

    dispatcher = InterceptionResumeDispatcher(_SystemStore())  # type: ignore[arg-type]
    asyncio.run(dispatcher.dispatch_once())

    assert queries[0]["point"] == {"$ne": InterceptionPoint.RESPONSE.value}


def test_the_dispatch_boundary_still_has_exactly_one_provider_call() -> None:
    """The second point must not have become a second way to reach a model.

    It sits after the one call and before the reply is used; it never issues one
    of its own, and the architecture test that enumerates `generate` call sites
    would fail if it did. Restated here as the property, next to the feature
    that would have broken it.
    """
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "return_platform"
        / "ai"
        / "gateway"
        / "interception_policy.py"
    ).read_text(encoding="utf-8")

    assert ".generate(" not in source


@pytest.mark.asyncio
async def test_the_dispatcher_records_the_edit_on_the_attempt_row() -> None:
    """End to end: an edit made through the real dispatch loop lands in
    telemetry as human-edited, not as the model that was called."""
    recorder = _Recorder()
    invoker, _, store = _harness(review=True, recorder=recorder)
    turn = asyncio.create_task(invoker.invoke(payload=_PAYLOAD, size_probe="small", log_context={}))
    held = await _first_pending(store)
    await store.answer(
        interception_id=held.interception_id,
        response_text=_EDITED_TEXT,
        answered_by="operator-7",
    )
    await turn

    success = [record for record in recorder.records if record.status == "SUCCESS"]
    assert len(success) == 1
    document = success[0].to_document()
    # The route that was called is still named -- it was called, and it was
    # billed -- but the row cannot be read as pure model output.
    assert document["provider"] == "GOOGLE"
    assert document["responseAttribution"] == "HUMAN_EDITED"
    assert document["humanEditedBy"] == "operator-7"


def test_the_dispatcher_is_not_required_to_opt_into_response_review() -> None:
    """`interception` has no default, deliberately: AI-01 was a control two of
    three callers never opted into. Response review defaults the other way, and
    the asymmetry is the point -- not reviewing a response is the status quo and
    leaves every existing check in place, whereas not gating a *request* was the
    security finding. A default here is what makes "off by default, no extra
    latency, no extra store write" true without editing every call site.
    """
    import inspect

    parameters = inspect.signature(FinalDispatcher.__init__).parameters
    assert parameters["interception"].default is inspect.Parameter.empty
    assert "response_interception" not in parameters, (
        "response review rides on the interception policy; a second parameter is "
        "a second thing every construction site can forget"
    )

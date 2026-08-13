"""AI-02: there is one final provider-dispatch boundary, and everything uses it.

Three modules used to call `AIProvider.generate`, each wrapped in its own copy of
the machinery around it -- its own candidate loop, its own failover bookkeeping,
its own attempt telemetry, and in one case its own cost arithmetic. They shared
the route *pool*, which is why the platform could describe itself as having one
AI path while behaving like three:

* interception was wired to the decision path's loop, so the path the Order
  Agent and the Graph Analyzer actually use had none (AI-01);
* recursive redaction had to be remembered per loop, and the simulator's loop
  never got it, so every simulated payload left the platform unmasked;
* cost came from the released catalog on two loops and from a local table that
  returned `0` for an unpriced model on the third.

None of those were introduced by anyone. They are what having three loops
*means*, and they will recur the moment a fourth appears. So the checks below are
deliberately of two kinds:

**Static**, so a fourth loop cannot be added without this file failing -- the
audit's verification step Y-4 is "enumerate every call site of
`AIProvider.generate`", and that enumeration is executable.

**Connection**, entering through the real production entry points rather than
through `FinalDispatcher` directly. A test that drives the dispatcher proves the
dispatcher works; only a test that drives `AIGatewayService.evaluate` and
`StructuredOutputInvoker.invoke` proves they cannot get to a provider any other
way.
"""

from __future__ import annotations

import ast
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from return_platform.ai.gateway import final_dispatch as final_dispatch_module
from return_platform.ai.gateway.final_dispatch import (
    DispatchDecision,
    DispatchRequest,
    FinalDispatcher,
    InterceptionPolicy,
    InterceptionVerdict,
)
from return_platform.ai.gateway.redaction import REDACTED
from return_platform.ai.gateway.service import AIGatewayService
from return_platform.ai.gateway.structured_invocation import (
    StructuredInvocationUnavailable,
    StructuredOutputInvoker,
)
from return_platform.ai.pricing import AIPricingStatus
from return_platform.ai.providers import ProviderError, ProviderRequest, ProviderResponse
from return_platform.ai.routing.routes import AIRoute
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import ModelTier, load_ai_gateway_configuration
from return_platform.configuration.settings import Settings
from return_platform.operations.models import AIGatewaySettingsView, AITraceView

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "return_platform"
CONFIG = Path(__file__).resolve().parents[1] / "config" / "ai_gateway.yaml"

DECISION_TASK = "RETURN_ELIGIBILITY_V1"

#: The one module permitted to hand a request to a provider.
DISPATCH_MODULE = "ai/gateway/final_dispatch.py"

#: Provider adapters build and consume `ProviderRequest` because they *are* the
#: providers; `replay.py` decorates one provider with another. Neither is a
#: dispatch boundary -- they are what the boundary dispatches *to*.
PROVIDER_PACKAGE = "ai/providers/"


# --------------------------------------------------------------------------
# Static: the Y-4 enumeration, executable
# --------------------------------------------------------------------------


def _python_sources() -> list[Path]:
    return sorted(SOURCE_ROOT.rglob("*.py"))


def _relative(path: Path) -> str:
    return path.relative_to(SOURCE_ROOT).as_posix()


def _provider_dispatch_sites() -> dict[str, list[int]]:
    """Every `<...>.provider.generate(...)` in the backend source tree.

    Matched on the *receiver* rather than on the method name alone, because
    `SimulationNarrativeService.generate` and `AIStudioService.generate` are
    unrelated methods that happen to share a verb. What identifies a real
    dispatch is that the thing being called is a route's provider.
    """
    sites: dict[str, list[int]] = {}
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "generate":
                continue
            receiver = ast.unparse(func.value)
            if receiver == "provider" or receiver.endswith(".provider"):
                sites.setdefault(_relative(path), []).append(node.lineno)
    return sites


def _provider_request_constructions() -> dict[str, list[int]]:
    """Every `ProviderRequest(...)`. Building one is the act of preparing an
    outbound model request, so it is a second, independent signal of a dispatch
    boundary that does not depend on how the call is spelled."""
    sites: dict[str, list[int]] = {}
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "ProviderRequest":
                    sites.setdefault(_relative(path), []).append(node.lineno)
    return sites


def test_exactly_one_module_dispatches_to_a_provider() -> None:
    """Y-4, as an assertion.

    Before AI-02 this returned three modules: `ai/gateway/service.py`,
    `ai/gateway/structured_invocation.py` and `dependency_simulation/ai.py`.
    """
    sites = _provider_dispatch_sites()
    assert set(sites) == {DISPATCH_MODULE}, (
        "a provider is dispatched to outside the single boundary: "
        f"{ {module: lines for module, lines in sites.items() if module != DISPATCH_MODULE} }"
    )


def test_the_boundary_holds_exactly_one_dispatch_expression() -> None:
    """One module containing two loops would satisfy the check above and
    reintroduce the defect, so the count matters as much as the location."""
    lines = _provider_dispatch_sites()[DISPATCH_MODULE]
    assert len(lines) == 1, f"the boundary dispatches from {len(lines)} places: lines {lines}"


def test_only_the_boundary_builds_an_outbound_provider_request() -> None:
    """A caller that assembles its own `ProviderRequest` is a caller that has
    started to grow a second loop, whether or not it has called `generate` yet."""
    offenders = {
        module: lines
        for module, lines in _provider_request_constructions().items()
        if module != DISPATCH_MODULE and not module.startswith(PROVIDER_PACKAGE)
    }
    assert offenders == {}, f"outbound provider requests are built outside the boundary: {offenders}"


def test_the_recursive_redactor_is_applied_by_the_boundary_itself() -> None:
    """Redaction that each caller must remember is redaction one caller will
    forget -- which is exactly how the simulator's payloads left unmasked. It is
    load-bearing that the mask is applied where the request is built."""
    source = (SOURCE_ROOT / DISPATCH_MODULE).read_text(encoding="utf-8")
    assert "redact_payload(dict(request.payload))" in source


# --------------------------------------------------------------------------
# Harness for the connection tests
# --------------------------------------------------------------------------


class _Answer(BaseModel):
    verdict: str


_ELIGIBILITY_TEXT = (
    '{"decision":"APPROVE","explanation":"Within the return window.",'
    '"confidenceMillionths":900000}'
)
_STRUCTURED_TEXT = '{"verdict":"ok"}'


class _CapturingProvider:
    """Records what actually left the platform.

    Deliberately captures the whole `ProviderRequest`: the redaction claim is
    about the bytes a provider receives, and asserting on anything earlier would
    test the assertion rather than the boundary.
    """

    configured = True

    def __init__(self, name: str, model: str, text: str, *, fail: bool = False) -> None:
        self.name = name
        self.model = model
        self._text = text
        self._fail = fail
        self.requests: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        if self._fail:
            raise ProviderError("PROVIDER_UNAVAILABLE")
        return ProviderResponse(
            provider=self.name,
            model=self.model,
            text=self._text,
            input_tokens=100,
            cached_input_tokens=None,
            output_tokens=20,
            total_tokens=120,
        )


class _Repository:
    """Only what `evaluate` touches, plus a counting attempt sink."""

    def __init__(self, *, intercept: bool = False) -> None:
        self.traces: dict[str, AITraceView] = {}
        self.attempts: list[dict[str, Any]] = []
        self.intercept = intercept

    async def create_ai_trace(self, **kwargs: Any) -> AITraceView:
        now = datetime.now(UTC)
        trace = AITraceView(
            id=str(uuid.uuid4()),
            sessionId=kwargs["session_id"],
            status=kwargs["status"],
            taskId=kwargs.get("task_id", DECISION_TASK),
            configuredTier=kwargs.get("configured_tier", "LIGHTWEIGHT"),
            promptVersion=kwargs["prompt_version"],
            redactedInput=kwargs["redacted_input"],
            systemPrompt=kwargs["system_prompt"],
            requestDigest=kwargs["request_digest"],
            originalRequestDigest=kwargs.get("original_request_digest"),
            safetyStatus=kwargs.get("safety_status", "SAFE"),
            safetySignals=kwargs.get("safety_signals", []),
            version=0,
            createdAt=now,
            updatedAt=now,
        )
        self.traces[trace.id] = trace
        return trace

    async def update_ai_trace(
        self, trace_id: str, updates: dict[str, Any], *, expected_version: int | None = None
    ) -> AITraceView:
        del expected_version
        payload = self.traces[trace_id].model_dump(mode="python")
        payload.update(updates)
        payload["version"] = payload["version"] + 1
        payload["updatedAt"] = datetime.now(UTC)
        updated = AITraceView.model_validate(payload)
        self.traces[trace_id] = updated
        return updated

    async def get_ai_settings(self) -> AIGatewaySettingsView:
        return AIGatewaySettingsView(
            interceptMode=self.intercept,
            providerOrder=["GOOGLE", "NVIDIA", "SIMULATOR"],
            version=0,
            updatedAt=datetime.now(UTC),
            updatedBy="test",
        )

    async def consume_ai_quota(self, bucket: str) -> bool:
        del bucket
        return True

    async def insert_ai_attempt_metric(self, document: dict[str, Any]) -> dict[str, Any]:
        self.attempts.append(document)
        return document


class _CountingPool(AIRoutePool):
    """A real pool that counts the operations the three loops each used to own."""

    def __init__(self, routes: tuple[AIRoute, ...], configuration: Any) -> None:
        super().__init__(routes, configuration)
        self.candidate_calls: list[str] = []
        self.successes = 0
        self.failures: list[str] = []

    async def candidates(self, task: Any, *, task_id: str | None = None, **kwargs: Any) -> Any:
        self.candidate_calls.append(task_id or task.tier.value)
        return await super().candidates(task, task_id=task_id, **kwargs)

    async def record_success(self, route: AIRoute) -> None:
        self.successes += 1
        await super().record_success(route)

    async def record_failure(self, route: AIRoute, error_code: str) -> None:
        self.failures.append(error_code)
        await super().record_failure(route, error_code)


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


def _route(provider: Any, tier: ModelTier, *, model_priority: int = 0) -> AIRoute:
    return AIRoute(
        route_id=f"google/{provider.model}/key-1",
        provider_name="GOOGLE",
        model=provider.model,
        credential_id="key-1",
        credential_fingerprint="test",
        tier=tier,
        provider=provider,
        provider_priority=0,
        model_priority=model_priority,
        credential_priority=0,
    )


def _structured_task_id(configuration: Any) -> str:
    for task_id, task in sorted(configuration.tasks.items()):
        if task.tier is ModelTier.STANDARD and "SIMULATOR" not in task.allowedProviders:
            return task_id
    pytest.skip("no STANDARD non-simulator task is configured")


def _invoker(
    configuration: Any, pool: AIRoutePool, **kwargs: Any
) -> StructuredOutputInvoker[_Answer]:
    return StructuredOutputInvoker(
        settings=_settings(),
        configuration=configuration,
        route_pool=pool,
        task_id=_structured_task_id(configuration),
        response_model=_Answer,
        logger=logging.getLogger("test"),
        event_prefix="test",
        subject="test invocation",
        **kwargs,
    )


_SAFE_INPUT: dict[str, Any] = {
    "customerReference": "CUS-1",
    "orderReferences": ["SO-1"],
    "itemReferences": ["LINE-1"],
    "reasonCode": "DEFECTIVE",
    "orderStatus": "DELIVERED",
    "daysSinceDelivery": 5,
}


# --------------------------------------------------------------------------
# Connection: both entry points traverse the boundary
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_entry_points_reach_a_provider_only_through_the_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patched on the *class*, so this cannot be satisfied by a caller that was
    handed a dispatcher and then ignored it. If either entry point still had its
    own loop, its task id would be missing from `seen` while its provider was
    still called."""
    loaded = load_ai_gateway_configuration(CONFIG)
    lightweight = _CapturingProvider("GOOGLE", "model-lightweight", _ELIGIBILITY_TEXT)
    standard = _CapturingProvider("GOOGLE", "model-standard", _STRUCTURED_TEXT)
    pool = _CountingPool(
        (
            _route(lightweight, ModelTier.LIGHTWEIGHT),
            _route(standard, ModelTier.STANDARD),
        ),
        loaded.configuration,
    )

    seen: list[str] = []
    original = FinalDispatcher.dispatch

    async def spy(self: FinalDispatcher, request: DispatchRequest, **kwargs: Any) -> Any:
        seen.append(request.task_id)
        return await original(self, request, **kwargs)

    monkeypatch.setattr(FinalDispatcher, "dispatch", spy)

    service = AIGatewayService(
        _Repository(), _settings(), loaded_configuration=loaded, route_pool=pool
    )
    result = await service.evaluate(session_id="RET-1", redacted_input=dict(_SAFE_INPUT))
    assert result.trace.decision == "APPROVE"

    invoker = _invoker(loaded.configuration, pool)
    invocation = await invoker.invoke(payload={"a": "b"}, size_probe="small", log_context={})
    assert invocation.value.verdict == "ok"

    assert lightweight.requests, "the decision path never reached its provider"
    assert standard.requests, "the structured path never reached its provider"
    assert set(seen) == {DECISION_TASK, _structured_task_id(loaded.configuration)}


@pytest.mark.asyncio
async def test_the_simulator_narrative_path_also_traverses_the_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The third loop. It is not mentioned in the AI-02 finding text because the
    audit's grep was for `interceptMode`, and this loop had never heard of it --
    which is precisely why it needed to be found by enumerating dispatch sites
    instead."""
    from return_platform.dependency_simulation.ai import SimulationNarrativeService
    from return_platform.dependency_simulation.configuration import (
        load_dependency_simulation_configuration,
    )
    from return_platform.dependency_simulation.models import DependencyKind
    from return_platform.dependency_simulation.repository import MemorySimulationRepository

    simulation_config = load_dependency_simulation_configuration(
        CONFIG.parent / "dependency_simulation.yaml"
    ).configuration
    loaded = load_ai_gateway_configuration(CONFIG)
    provider = _CapturingProvider(
        "GOOGLE",
        "light-model-a",
        '{"message":"Done.","summary":"Simulated.","nextAction":"Continue."}',
    )
    pool = _CountingPool((_route(provider, ModelTier.LIGHTWEIGHT),), loaded.configuration)

    calls: list[str] = []
    original = FinalDispatcher.dispatch

    async def spy(self: FinalDispatcher, request: DispatchRequest, **kwargs: Any) -> Any:
        calls.append(request.task_id)
        return await original(self, request, **kwargs)

    monkeypatch.setattr(FinalDispatcher, "dispatch", spy)

    narratives = SimulationNarrativeService(
        MemorySimulationRepository(),
        _settings(),
        simulation_config,
        loaded_ai_gateway=loaded,
        route_pool=pool,
    )
    outcome = await narratives.generate(
        operation_id="OP-1",
        session_id="RET-SIM-1",
        dependency=DependencyKind.OMC,
        operation="CREATE_RMA",
        result={"items": [{"quantity": 1}]},
        enabled=True,
    )

    assert outcome.narrative.source == "LIGHTWEIGHT_AI"
    assert provider.requests, "the simulator never reached its provider"
    assert calls == [simulation_config.ai.taskId]


# --------------------------------------------------------------------------
# Connection: one implementation of each shared concern, run once
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_selection_failover_and_telemetry_each_run_once_per_attempt() -> None:
    """One failing route then a healthy one, through the structured path.

    The counts are the point. Two loops meant two candidate queries, two
    failover bookkeepers and two telemetry writers for what an operator reads as
    one invocation.
    """
    loaded = load_ai_gateway_configuration(CONFIG)
    failing = _CapturingProvider("GOOGLE", "model-a", _STRUCTURED_TEXT, fail=True)
    healthy = _CapturingProvider("GOOGLE", "model-b", _STRUCTURED_TEXT)
    pool = _CountingPool(
        (
            _route(failing, ModelTier.STANDARD, model_priority=0),
            _route(healthy, ModelTier.STANDARD, model_priority=1),
        ),
        loaded.configuration,
    )
    repository = _Repository()
    from return_platform.ai.gateway.telemetry import RepositoryAIAttemptRecorder

    invoker = _invoker(
        loaded.configuration, pool, recorder=RepositoryAIAttemptRecorder(repository)
    )
    result = await invoker.invoke(payload={"a": "b"}, size_probe="small", log_context={})

    assert result.value.verdict == "ok"
    # Route selection: once. A second query would mean a second selector.
    assert len(pool.candidate_calls) == 1, pool.candidate_calls
    # Failover: exactly one failure and one success recorded, by one bookkeeper.
    assert pool.failures == ["PROVIDER_UNAVAILABLE"]
    assert pool.successes == 1
    # Telemetry: one row per attempt, no more and no fewer.
    assert [document["status"] for document in repository.attempts] == ["FAILED", "SUCCESS"]
    assert len({document["traceId"] for document in repository.attempts}) == 1


@pytest.mark.asyncio
async def test_each_recorded_attempt_is_priced_exactly_once() -> None:
    """The decision path used to call the pricing catalog twice for one
    successful attempt -- once for the trace's four pricing columns and once for
    the attempt row -- which is two chances for the trace and its own attempts to
    disagree about what a call cost."""
    loaded = load_ai_gateway_configuration(CONFIG)
    provider = _CapturingProvider("GOOGLE", "model-lightweight", _ELIGIBILITY_TEXT)
    pool = _CountingPool((_route(provider, ModelTier.LIGHTWEIGHT),), loaded.configuration)
    repository = _Repository()

    estimates: list[tuple[str | None, str | None]] = []
    catalog = loaded.configuration.pricing
    original_estimate = type(catalog).estimate

    def counting_estimate(self: Any, **kwargs: Any) -> Any:
        estimates.append((kwargs.get("provider"), kwargs.get("model")))
        return original_estimate(self, **kwargs)

    service = AIGatewayService(
        repository, _settings(), loaded_configuration=loaded, route_pool=pool
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(type(catalog), "estimate", counting_estimate)
        result = await service.evaluate(session_id="RET-2", redacted_input=dict(_SAFE_INPUT))

    assert result.trace.decision == "APPROVE"
    assert len(estimates) == len(repository.attempts) == 1, (
        f"{len(estimates)} price(s) for {len(repository.attempts)} attempt row(s)"
    )


@pytest.mark.asyncio
async def test_a_model_the_catalog_does_not_cover_is_unknown_and_never_zero() -> None:
    """The pricing integrity C7 protects: a gap must be visible in the record
    rather than absorbed into a total that looks complete. The shipped catalog
    does not price this test model."""
    loaded = load_ai_gateway_configuration(CONFIG)
    provider = _CapturingProvider("GOOGLE", "model-lightweight", _ELIGIBILITY_TEXT)
    pool = _CountingPool((_route(provider, ModelTier.LIGHTWEIGHT),), loaded.configuration)
    repository = _Repository()
    service = AIGatewayService(
        repository, _settings(), loaded_configuration=loaded, route_pool=pool
    )

    result = await service.evaluate(session_id="RET-3", redacted_input=dict(_SAFE_INPUT))

    assert result.trace.pricingStatus == AIPricingStatus.UNKNOWN.value
    assert result.trace.estimatedCostMicros is None
    document = repository.attempts[-1]
    assert document["pricingStatus"] == AIPricingStatus.UNKNOWN.value
    assert document["estimatedCostMicros"] is None


# --------------------------------------------------------------------------
# Connection: redaction and interception now cover both paths
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redaction_reaches_a_json_encoded_string_on_the_decision_path() -> None:
    """`_redact_and_validate` rejects a sensitive *key*; it cannot see inside a
    string that is itself a document. That is the case the recursive redactor
    exists for, and it must apply on this path too."""
    loaded = load_ai_gateway_configuration(CONFIG)
    provider = _CapturingProvider("GOOGLE", "model-lightweight", _ELIGIBILITY_TEXT)
    pool = _CountingPool((_route(provider, ModelTier.LIGHTWEIGHT),), loaded.configuration)
    service = AIGatewayService(
        _Repository(), _settings(), loaded_configuration=loaded, route_pool=pool
    )

    await service.evaluate(
        session_id="RET-4",
        redacted_input={
            **_SAFE_INPUT,
            "reasonCode": '{"customer_name":"Jane Doe","sku":"SKU-1"}',
        },
    )

    sent = str(provider.requests[-1].user_payload)
    assert "Jane Doe" not in sent
    assert REDACTED in sent
    # Masks leaves only: the non-sensitive sibling must survive, or the model
    # loses the schema it reasons over.
    assert "SKU-1" in sent


@pytest.mark.asyncio
async def test_redaction_reaches_a_nested_value_on_the_structured_path() -> None:
    """The path the Order Agent uses. Its payload is five keys of which one is a
    JSON string holding every graph row the agent retrieved."""
    loaded = load_ai_gateway_configuration(CONFIG)
    provider = _CapturingProvider("GOOGLE", "model-standard", _STRUCTURED_TEXT)
    pool = _CountingPool((_route(provider, ModelTier.STANDARD),), loaded.configuration)
    invoker = _invoker(loaded.configuration, pool)

    await invoker.invoke(
        payload={
            "contextJson": '{"rows":[{"customer_email":"jane@example.test","orderId":"SO-9"}]}',
            "compactSchema": {"customer_email": {"searchable": True}},
        },
        size_probe="small",
        log_context={},
    )

    sent = str(provider.requests[-1].user_payload)
    assert "jane@example.test" not in sent
    assert REDACTED in sent
    assert "SO-9" in sent
    # A metadata object under a sensitive key describes a field the agent may
    # search; blanking it would leave the agent unable to plan a query at all.
    assert "searchable" in sent


class _HoldEverything(InterceptionPolicy):
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def decide(self, request: DispatchRequest) -> InterceptionVerdict:
        self.asked.append(request.task_id)
        return InterceptionVerdict(
            decision=DispatchDecision.HUMAN_RESPONSE, reason="INTERCEPTION_PENDING"
        )


@pytest.mark.asyncio
async def test_the_boundary_can_hold_a_structured_invocation_before_any_provider() -> None:
    """AI-01's seam, proven to exist in Wave 0.

    Interception was real, audited and operator-visible on the decision path and
    structurally impossible on the structured one, because the gate was an `if`
    inside a loop the structured path did not run. It is now a decision the
    boundary takes for every caller, so wiring a real policy to this path is a
    configuration change rather than a second implementation.
    """
    loaded = load_ai_gateway_configuration(CONFIG)
    provider = _CapturingProvider("GOOGLE", "model-standard", _STRUCTURED_TEXT)
    pool = _CountingPool((_route(provider, ModelTier.STANDARD),), loaded.configuration)
    policy = _HoldEverything()
    invoker = _invoker(loaded.configuration, pool, interception=policy)

    with pytest.raises(StructuredInvocationUnavailable, match="HUMAN_RESPONSE"):
        await invoker.invoke(payload={"a": "b"}, size_probe="small", log_context={})

    assert policy.asked == [_structured_task_id(loaded.configuration)]
    assert provider.requests == [], "an intercepted request must never reach a provider"


@pytest.mark.asyncio
async def test_intercept_mode_still_holds_the_decision_path() -> None:
    """The behaviour that already existed, preserved through the move: the same
    `interceptMode` flag, the same `INTERCEPTION_PENDING` status, the same
    `pending_interception` answer -- now expressed as the boundary's policy."""
    loaded = load_ai_gateway_configuration(CONFIG)
    provider = _CapturingProvider("GOOGLE", "model-lightweight", _ELIGIBILITY_TEXT)
    pool = _CountingPool((_route(provider, ModelTier.LIGHTWEIGHT),), loaded.configuration)
    service = AIGatewayService(
        _Repository(intercept=True), _settings(), loaded_configuration=loaded, route_pool=pool
    )

    result = await service.evaluate(session_id="RET-5", redacted_input=dict(_SAFE_INPUT))

    assert result.pending_interception is True
    assert result.trace.status == "INTERCEPTION_PENDING"
    assert provider.requests == []


@pytest.mark.asyncio
async def test_an_intercepted_request_does_not_spend_the_session_quota() -> None:
    """Ordering, not decoration. The quota is a precondition of *dispatching*;
    charging it for a request a human answered would let interception exhaust a
    session's budget without a single provider call."""
    loaded = load_ai_gateway_configuration(CONFIG)
    provider = _CapturingProvider("GOOGLE", "model-lightweight", _ELIGIBILITY_TEXT)
    pool = _CountingPool((_route(provider, ModelTier.LIGHTWEIGHT),), loaded.configuration)
    repository = _Repository(intercept=True)
    consumed: list[str] = []

    async def consume(bucket: str) -> bool:
        consumed.append(bucket)
        return True

    repository.consume_ai_quota = consume  # type: ignore[method-assign]
    service = AIGatewayService(
        repository, _settings(), loaded_configuration=loaded, route_pool=pool
    )

    await service.evaluate(session_id="RET-6", redacted_input=dict(_SAFE_INPUT))

    assert consumed == []


def test_the_default_policy_is_a_named_object_rather_than_an_omission() -> None:
    """`ALLOW_ALL` is greppable; a missing argument is not. AI-01's remaining
    work is exactly "replace each `ALLOW_ALL` with a real policy", and that is a
    reviewable list only because the default has a name."""
    assert isinstance(final_dispatch_module.ALLOW_ALL, InterceptionPolicy)
    dispatcher = FinalDispatcher(
        settings=_settings(),
        configuration=load_ai_gateway_configuration(CONFIG).configuration,
        route_pool=AIRoutePool((), load_ai_gateway_configuration(CONFIG).configuration),
    )
    assert dispatcher.interception is final_dispatch_module.ALLOW_ALL

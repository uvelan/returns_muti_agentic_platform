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
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from return_platform.ai.gateway import final_dispatch as final_dispatch_module
from return_platform.ai.gateway.final_dispatch import (
    ALLOW_ALL,
    DispatchDecision,
    DispatchRequest,
    FinalDispatcher,
    InterceptionPolicy,
    InterceptionVerdict,
)
from return_platform.ai.gateway.interception_policy import build_interception_policy
from return_platform.ai.gateway.redaction import REDACTED
from return_platform.ai.gateway.service import AIGatewayService
from return_platform.ai.gateway.structured_invocation import (
    StructuredInvocationUnavailable,
    StructuredOutputInvoker,
)
from return_platform.ai.interception.records import (
    Interception,
    InterceptionStatus,
    ResumeCommand,
)
from return_platform.ai.pricing import AIPricingStatus
from return_platform.ai.providers import ProviderError, ProviderRequest, ProviderResponse
from return_platform.ai.providers.schema_cleaner import clean_gemini_schema
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
    assert offenders == {}, (
        f"outbound provider requests are built outside the boundary: {offenders}"
    )


def test_the_recursive_redactor_runs_before_the_interception_decision() -> None:
    """Redaction each caller must remember is redaction one caller will forget --
    which is exactly how the simulator's payloads left unmasked.

    The *ordering* became load-bearing with AI-01: an interception policy
    persists the held request for an operator to read, so masking after the
    policy runs would seal customer data into a store the provider itself never
    received. Asserted structurally rather than only behaviourally, because the
    behavioural version passes for any payload that happens to contain nothing
    sensitive.
    """
    source = (SOURCE_ROOT / DISPATCH_MODULE).read_text(encoding="utf-8")
    redaction_at = source.index("redact_payload(dict(request.payload))")
    interception_at = source.index("self._interception.decide(request)")
    dispatch_at = source.index("route.provider.generate(")
    assert redaction_at < interception_at < dispatch_at, (
        "the payload must be masked before it can be persisted or dispatched"
    )


# --------------------------------------------------------------------------
# Harness for the connection tests
# --------------------------------------------------------------------------


class _Answer(BaseModel):
    verdict: str


_ELIGIBILITY_TEXT = (
    '{"decision":"APPROVE","explanation":"Within the return window.","confidenceMillionths":900000}'
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
        # Overridable via kwargs; the interception-specific cases in
        # test_ai_interception_covers_every_path.py pass a real policy.
        **{"interception": ALLOW_ALL, **kwargs},
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

    invoker = _invoker(loaded.configuration, pool, recorder=RepositoryAIAttemptRecorder(repository))
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


class _RecordingInterceptionStore:
    """The subset of `InterceptionStore` that `DurableInterceptionPolicy` calls.

    Keeps what it was handed verbatim rather than sealing it: the claim under
    test is about the bytes the *policy* passed to persistence, and encrypting
    them here would hide exactly the thing being compared. The real store's
    sealing, compare-and-set transitions and metadata allowlist are exercised
    against Mongo in `test_durable_interception_real_infra.py`.
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

    async def allow(self, *, interception_id: str, allowed_by: str) -> Interception:
        record = self.records[interception_id]
        approved = Interception(
            interception_id=record.interception_id,
            task_id=record.task_id,
            status=InterceptionStatus.ALLOWED,
            resume=record.resume,
            created_at=record.created_at,
            expires_at=record.expires_at,
            answered_at=datetime.now(UTC),
            answered_by=allowed_by,
        )
        self.records[interception_id] = approved
        return approved

    # The rest of the contract is not on the path this file tests, and a
    # half-implementation would be a worse answer than none: the transitions have
    # compare-and-set semantics that only the real store can honour, and they are
    # exercised in `test_ai_interception_covers_every_path.py`.
    async def answer(
        self, *, interception_id: str, response_text: str, answered_by: str
    ) -> Interception:
        raise NotImplementedError

    async def cancel(self, *, interception_id: str, status: InterceptionStatus) -> None:
        raise NotImplementedError

    async def list_pending(self, *, limit: int = 100) -> list[Interception]:
        return [
            record
            for record in self.records.values()
            if record.status is InterceptionStatus.PENDING
        ][:limit]


class _InterceptionOn:
    async def get_ai_settings(self) -> Any:
        class _View:
            interceptMode = True

        return _View()


@pytest.mark.asyncio
async def test_the_held_request_is_never_richer_than_what_the_provider_receives() -> None:
    """The single-boundary property stated as an equality rather than as an order.

    `test_the_recursive_redactor_runs_before_the_interception_decision` asserts
    the *source* order, which is a proxy: it reads line positions and cannot see
    control flow, so a redaction call moved inside a branch would still satisfy
    it. This asserts the consequence directly, at both sinks, on one payload.

    They are the same payload only because redaction happens once, above both. If
    it moved back to `ProviderRequest` construction -- where it lived until AI-01
    -- the persisted copy would carry `jane@example.test` and the dispatched copy
    would not, and an operator console would be the richer of the two readers of
    a customer's data. That is the inversion this equality makes impossible to
    reintroduce quietly.
    """
    loaded = load_ai_gateway_configuration(CONFIG)
    provider = _CapturingProvider("GOOGLE", "model-standard", _STRUCTURED_TEXT)
    pool = _CountingPool((_route(provider, ModelTier.STANDARD),), loaded.configuration)
    store = _RecordingInterceptionStore()
    invoker = _invoker(
        loaded.configuration,
        pool,
        interception=build_interception_policy(
            store=store, settings_source=_InterceptionOn(), subject="test"
        ),
    )
    payload = {
        "mode": "DECIDE",
        "contextJson": '{"rows":[{"customer_email":"jane@example.test","orderId":"SO-9"}]}',
    }

    # Held: nothing dispatched, one record persisted.
    with pytest.raises(StructuredInvocationUnavailable, match="HUMAN_RESPONSE"):
        await invoker.invoke(payload=dict(payload), size_probe="small", log_context={})
    assert provider.requests == []
    persisted = dict(next(iter(store.payloads.values()))["payload"])

    # Approved, then resumed. Same payload, therefore the same derived id,
    # therefore the same interception -- which is now ALLOWED.
    held = await store.list_pending()
    await store.allow(interception_id=held[0].interception_id, allowed_by="operator-1")
    await invoker.invoke(payload=dict(payload), size_probe="small", log_context={})

    dispatched = dict(provider.requests[-1].user_payload)
    assert persisted == dispatched, (
        "the interception store and the provider disagree about what was sent: "
        f"held={persisted} dispatched={dispatched}"
    )
    # And neither is the raw payload -- an equality between two unredacted copies
    # would satisfy the line above while leaking from both sinks at once.
    assert "jane@example.test" not in json.dumps(persisted)
    assert REDACTED in json.dumps(persisted)
    assert "SO-9" in json.dumps(persisted)


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


# --------------------------------------------------------------------------
# Connection: configuration freshness is one question with one answer
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_activated_release_changes_the_next_structured_invocation() -> None:
    """Task-level AI configuration used to be pinned for the life of a process.

    `StructuredOutputInvoker.__init__` took a copy of the task and of the
    configuration, so promptVersion, tier, token ceilings, allowed providers,
    retry limits and pricing were all frozen at construction -- everywhere,
    including the API, and even though `AIRoutePool.replace_routes` had already
    swapped the pool's own document. Routes hot-applied and the settings around
    them did not, which is the worst of the two failure modes: the process looks
    like it adopted the release.

    Driven through `replace_routes`, the real activation path, rather than by
    reaching into the invoker.
    """
    loaded = load_ai_gateway_configuration(CONFIG)
    provider = _CapturingProvider("GOOGLE", "model-standard", _STRUCTURED_TEXT)
    route = _route(provider, ModelTier.STANDARD)
    pool = _CountingPool((route,), loaded.configuration)
    invoker = _invoker(loaded.configuration, pool)
    task_id = _structured_task_id(loaded.configuration)

    await invoker.invoke(payload={"a": "b"}, size_probe="small", log_context={})
    assert loaded.configuration.tasks[task_id].systemPrompt in provider.requests[-1].system_prompt

    released = loaded.configuration.model_copy(
        update={
            "tasks": {
                **loaded.configuration.tasks,
                task_id: loaded.configuration.tasks[task_id].model_copy(
                    update={
                        "systemPrompt": "RELEASED PROMPT v2",
                        "maximumOutputTokens": 321,
                    }
                ),
            }
        }
    )
    await pool.replace_routes((route,), released)

    await invoker.invoke(payload={"a": "b"}, size_probe="small", log_context={})

    assert provider.requests[-1].system_prompt.startswith("RELEASED PROMPT v2")
    assert provider.requests[-1].max_output_tokens == 321
    # And the invoker reports the released task, not the one it was built with.
    assert invoker.task.maximumOutputTokens == 321


@pytest.mark.asyncio
async def test_an_activated_release_changes_the_next_decision_invocation() -> None:
    """The same question, answered the same way on the other entry point --
    which is the point of having one boundary. `AIGatewayService.configuration`
    is what `/ai/tasks` reports, so a stale copy also made the console describe a
    release the process was no longer running."""
    loaded = load_ai_gateway_configuration(CONFIG)
    provider = _CapturingProvider("GOOGLE", "model-lightweight", _ELIGIBILITY_TEXT)
    route = _route(provider, ModelTier.LIGHTWEIGHT)
    pool = _CountingPool((route,), loaded.configuration)
    service = AIGatewayService(
        _Repository(), _settings(), loaded_configuration=loaded, route_pool=pool
    )

    await service.evaluate(session_id="RET-7", redacted_input=dict(_SAFE_INPUT))
    assert provider.requests[-1].max_output_tokens == (
        loaded.configuration.tasks[DECISION_TASK].maximumOutputTokens
    )

    released = loaded.configuration.model_copy(
        update={
            "tasks": {
                **loaded.configuration.tasks,
                DECISION_TASK: loaded.configuration.tasks[DECISION_TASK].model_copy(
                    update={"maximumOutputTokens": 77, "promptVersion": "released-v9"}
                ),
            }
        }
    )
    await pool.replace_routes((route,), released)

    result = await service.evaluate(session_id="RET-8", redacted_input=dict(_SAFE_INPUT))

    assert provider.requests[-1].max_output_tokens == 77
    assert result.trace.promptVersion == "released-v9"
    assert service.configuration.tasks[DECISION_TASK].maximumOutputTokens == 77


def test_the_boundary_takes_no_configuration_of_its_own() -> None:
    """The structural guarantee behind the two tests above.

    A `configuration` constructor argument would let a dispatcher answer "which
    document is in force" differently from the pool it dispatches over, and the
    copy would go stale exactly as the invoker's did. The pool is the only object
    an activation updates atomically, so it is the only source.
    """
    import inspect

    parameters = inspect.signature(FinalDispatcher.__init__).parameters
    assert "configuration" not in parameters, (
        "the dispatch boundary must read the active configuration from the route "
        "pool, not hold a copy"
    )


def test_an_ungated_path_is_stated_rather_than_defaulted() -> None:
    """`ALLOW_ALL` survives AI-01 as a legitimate answer -- a process with no
    interception store cannot hold a request -- but it is no longer a *default*.
    It has to be typed at the call site, which is what turns "which paths are
    ungated" from an audit finding into a grep."""
    assert isinstance(final_dispatch_module.ALLOW_ALL, InterceptionPolicy)
    dispatcher = FinalDispatcher(
        settings=_settings(),
        route_pool=AIRoutePool((), load_ai_gateway_configuration(CONFIG).configuration),
        interception=final_dispatch_module.ALLOW_ALL,
    )
    assert dispatcher.interception is final_dispatch_module.ALLOW_ALL


# --------------------------------------------------------------------------
# PERF-03: the cacheable prefix, and what makes it cacheable
# --------------------------------------------------------------------------
#
# The canonical STANDARD route is Gemini Flash with NVIDIA Nemotron behind it,
# and neither adapter sends a caching directive: what that route offers is
# *automatic prefix caching*, which is earned rather than requested. The whole of
# the requirement is therefore one structural property -- everything stable comes
# first, byte-identical, and nothing per-turn gets in front of it.
#
# It is currently satisfied, and by a convention nothing enforced. `invoke`
# appends `prompt_addendum` after the system prompt and the response schema, and
# its docstring says why; a single edit moving the turn's as-of instant ahead of
# them would make every request's first bytes differ from the last and no test
# would notice, because a cache miss is invisible from inside the process.
#
# Measured on this tree, driving `RoutePoolReasoningModelGateway.decide` with the
# packaged active schema (`config/dynamic_knowledge/active-schema.return-order.yaml`,
# `order-discovery-agent`, 12 entities / 149 fields) over three turns of one
# conversation:
#
#   system_prompt                     21,933 chars   (identical for all 3 turns
#                                                     up to char 21,066 ~5,266
#                                                     tokens; the divergence is
#                                                     inside the temporal
#                                                     addendum, which is last)
#   user_payload                     ~45,000 chars   grows with the transcript
#   total per turn                   ~67,000 chars  ~16,750 tokens
#   stable share                            ~31 %
#
# The audit's P-7 direction names "the invariant schema prefix, which dominates
# contextJson". It does dominate -- `compact_schema` is 39,776 of the ~45,000
# payload chars -- but it is not a *prefix*. `contextJson` is dumped with
# `sort_keys=True`, and the first key that changes between turns is `as_of`, at
# position 1 of 25; `compact_schema` is at position 7, behind it. So those ~9,900
# tokens sit after the first differing byte and no prefix cache can reach them.
# Moving them in front would mean either dropping `sort_keys=True` -- which is
# what makes `request_digest`, and therefore the derived interception id, stable
# for identical content -- or hoisting `compact_schema` out of `contextJson` in
# `dynamic_knowledge/integration/model_gateway.py` and rewording the prompt that
# reads `contextJson.compact_schema.strongAnchors`. Neither is an AI-gateway
# change.
#
# An *explicit* cache (Gemini `cachedContents`, Anthropic `cache_control`) is
# refused for a separate and harder reason: a handle is provider-scoped, and one
# invocation may traverse GOOGLE -> NVIDIA -> OPENAI -> ANTHROPIC -> OLLAMA
# inside a single `dispatch` failover loop. Carrying one on `ProviderRequest`
# would make the shared outbound contract provider-specific, which is AI-02 run
# backwards. And nothing in `contextJson` may be cached at all: it carries the
# transcript, captured facts and retrieved graph rows.
#
# What is left is worth guarding, and that is what these two tests do.


def _shared_prefix_length(prompts: list[str]) -> int:
    """How many leading characters every request has in common.

    This is the quantity a provider matches on, so it is the quantity the tests
    assert -- not "are the prompts equal", which they are not and must not be.
    """
    shortest = min(len(prompt) for prompt in prompts)
    shared = 0
    while shared < shortest and len({prompt[shared] for prompt in prompts}) == 1:
        shared += 1
    return shared


async def _reasoning_prefix_probe() -> tuple[list[str], list[str]]:
    """Three turns and all three call modes, through the real Order Agent gateway.

    Driven through `RoutePoolReasoningModelGateway` rather than through
    `StructuredOutputInvoker` directly because the invoker is not the thing that
    could break this: the caller is. It is the caller that decides what is a
    prompt addendum and what is payload, and a per-turn value promoted into the
    prompt is the regression being guarded against.
    """
    from return_platform.dynamic_knowledge.integration.model_gateway import (
        RoutePoolReasoningModelGateway,
    )
    from return_platform.dynamic_knowledge.order_agent.contracts import AgentTurnContext

    loaded = load_ai_gateway_configuration(CONFIG)
    action = json.dumps(
        {
            "business_capability": "order-discovery",
            "action_type": "CLARIFY",
            "decision_summary": "One more detail needed.",
            "response": {
                "status": "NEEDS_CLARIFICATION",
                "business_capability": "order-discovery",
                "statements": [],
                "suggestions": [],
                "requested_input": "Which order?",
            },
        }
    )
    provider = _CapturingProvider("GOOGLE", "models/gemini-3.6-flash", action)
    pool = _CountingPool((_route(provider, ModelTier.STANDARD),), loaded.configuration)
    gateway = RoutePoolReasoningModelGateway(
        settings=_settings(),
        configuration=loaded.configuration,
        route_pool=pool,
    )

    def context(turn: int) -> AgentTurnContext:
        return AgentTurnContext(
            conversation_id="conv-1",
            client_turn_id=f"turn-{turn}",
            agent_id="order-discovery-agent",
            # Deliberately not a plausible utterance: the prompt itself quotes
            # "the damaged red pump from order CW273354" as a worked example, and
            # a canary that collides with prompt text tests nothing.
            user_message=f"canary-utterance-{turn}",
            # Every one of these differs per turn, which is the point: none of
            # them may appear before the end of the stable prefix.
            as_of=datetime(2026, 8, 14, 9, 30 + turn, tzinfo=UTC),
            session_timezone="America/Chicago",
            schema_version="v1",
            graph_generation_id="generation-1",
            configuration_release_id="release-1",
            policy_version="p1",
            prompt_version="dynamic-order-agent-reasoning-v13",
            compact_schema={"entities": {"sales_order": {"fields": {}}}},
            conversation_state={},
            transcript=tuple({"role": "associate", "text": f"m{i}"} for i in range(turn)),
        )

    for turn in (1, 2, 3):
        await gateway.decide(context(turn))
    # The correction modes carry their extra material in the payload; if one of
    # them ever put it in the prompt the prefix would fork by call mode and only
    # two thirds of the traffic would ever hit a cache.
    await gateway.correct_action(
        context=context(4), invalid_action=None, validation_error="bad statement_type"
    )

    return [request.system_prompt for request in provider.requests], [
        json.dumps(request.user_payload) for request in provider.requests
    ]


@pytest.mark.asyncio
async def test_the_cacheable_prefix_is_byte_identical_across_turns_and_modes() -> None:
    """The precondition for automatic prefix caching, asserted on the wire.

    Not "the prompt is the same" -- it is not, the temporal addendum changes
    every turn -- but "the same leading bytes", which is what a provider matches
    on. The length floor is deliberate: without it the test would pass on a
    single shared newline, which is technically a common prefix and worth
    nothing.
    """
    prompts, _ = await _reasoning_prefix_probe()
    assert len(prompts) == 4
    assert len(set(prompts)) > 1, (
        "every prompt is identical, so a shared-prefix measurement over them "
        "would be measuring nothing"
    )

    from return_platform.dynamic_knowledge.order_agent.contracts import AgentAction
    from return_platform.dynamic_knowledge.order_agent.reasoning_stage import (
        reasoning_stage,
        stage_task_id,
    )

    shared = _shared_prefix_length(prompts)
    # Which prompt *should* be on the wire is asked of the classifier rather
    # than hardcoded. Every call in the probe carries an empty
    # `conversation_state` and no `case_id`, so all four are the same stage --
    # which is exactly why a shared-prefix measurement over them still means
    # something. Caching is now a within-stage guarantee: turns that stay in one
    # stage share the whole head, and a turn that changes stage is a turn whose
    # situation changed, which is the trade the split was made for.
    expected_task_id = stage_task_id(reasoning_stage(case_id=None, conversation_state={}))
    task = load_ai_gateway_configuration(CONFIG).configuration.tasks[expected_task_id]
    schema_block = len(
        "\n\nREQUIRED RESPONSE SCHEMA (Output exactly this JSON structure):\n```json\n"
        + json.dumps(clean_gemini_schema(AgentAction.model_json_schema()))
        + "\n```"
    )

    assert prompts[0].startswith(task.systemPrompt), (
        f"the configured prompt for this turn's stage ({expected_task_id}) is no "
        "longer the first thing on the wire"
    )
    # Compared against the *computed* stable head rather than a magic number, so
    # the assertion follows an ordinary prompt or schema edit and still fails on
    # a structural one. Both pieces are stable for a release; anything wedged
    # between them truncates the cacheable region to wherever it landed.
    assert shared >= len(task.systemPrompt) + schema_block, (
        f"only {shared} leading chars are shared across turns, short of the "
        f"{len(task.systemPrompt) + schema_block}-char stable head (prompt + "
        "response schema) -- something per-turn has been moved in front of or "
        "inside it, and everything after that point is uncacheable"
    )


@pytest.mark.asyncio
async def test_no_per_turn_value_is_copied_into_the_system_prompt() -> None:
    """The other half, and the one a shared-prefix measurement cannot express.

    A cross-turn comparison can never find a per-turn value *in* the shared
    prefix -- by construction, a value that varies is where the prefix ends. What
    it cannot see is a per-turn value copied into the prompt at all, which is the
    regression that actually gets written: the model keeps missing something in
    `contextJson`, so someone promotes it into the system prompt, and the
    divergence point moves from the end of a 21,000-char prefix to wherever they
    put it. So this asserts over the whole prompt, not over the common part.

    The as-of instant is the single deliberate exception. It is in the prompt
    because a model that has to find "now" inside a sorted JSON blob will
    sometimes not look, and it is *last* for exactly the reason this file is
    about.
    """
    prompts, payloads = await _reasoning_prefix_probe()

    for per_turn in ("turn-1", "canary-utterance-1", "bad statement_type"):
        assert per_turn not in prompts[0] and per_turn not in prompts[-1], (
            f"the per-turn value {per_turn!r} has been copied into the system "
            "prompt; the cacheable prefix now ends where it appears"
        )
    # ...and it really is being sent, in the payload, where it belongs. Without
    # this the assertions above would hold for a request that carried nothing.
    assert "canary-utterance-1" in payloads[0]
    assert "bad statement_type" in payloads[-1]

    # The exception, pinned at both ends: present, and after the stable head.
    addendum_at = prompts[0].index("2026-08-14T09:31")
    assert addendum_at > _shared_prefix_length(prompts) - 32, (
        "the turn's as-of instant has moved out of the trailing addendum and "
        "into the body of the prompt"
    )


# --------------------------------------------------------------------------
# The prompt and the payload have to agree about names
# --------------------------------------------------------------------------


def test_every_context_path_the_prompt_names_exists_in_the_turn_context() -> None:
    """The reasoning prompt navigates `contextJson` by path, and nothing checked
    that the paths were real.

    `ORDER_AGENT_REASONING_V1` names six blocks -- the compact schema's strong
    anchors, the captured facts, the suggested discriminators, the transcript,
    the identification catalogue and the order-search cache -- and the model can
    only follow an instruction to a block it can find. `AgentTurnContext`
    declares no alias generator and `model_gateway` dumps it with
    `model_dump(mode="json")` and no `by_alias`, so every top-level key in
    `contextJson` is the field name verbatim, in snake_case.

    This found one: the prompt said `contextJson.conversationState.orderSearchCache`
    while the field is `conversation_state`, so the paragraph teaching the agent
    to serve "show next" from the cached page pointed at a key that is not in the
    document. The camelCase names elsewhere in the prompt are legitimate -- they
    are dict keys built inside `compact_schema` and `orderSearchCache`, not model
    fields -- which is exactly why a reader does not catch this by eye.
    """
    import re

    from return_platform.dynamic_knowledge.order_agent.contracts import AgentTurnContext

    task = load_ai_gateway_configuration(CONFIG).configuration.tasks["ORDER_AGENT_REASONING_V1"]
    named = {
        match.split(".")[1]
        for match in re.findall(r"contextJson\.[A-Za-z_][A-Za-z0-9_.]*", task.systemPrompt)
    }

    # Liveness: the prompt genuinely does navigate by path, so a regex that
    # stopped matching would otherwise leave this passing on an empty set.
    assert len(named) >= 5, f"the contextJson path scan found only {named}"

    unknown = sorted(name for name in named if name not in AgentTurnContext.model_fields)
    assert unknown == [], (
        f"the reasoning prompt sends the model to contextJson keys that do not "
        f"exist: {unknown}. Available: {sorted(AgentTurnContext.model_fields)}"
    )


def test_the_prompt_names_the_catalogue_that_makes_a_new_field_reachable() -> None:
    """C6's model-facing half, stated in the prompt rather than left to inference.

    Adding the tenth identification field requires zero Python edits, and it
    requires zero *prompt* edits because the field describes itself to the model
    through `contextJson.identification_fields` -- intent key, label, aliases,
    whether any search can use it, and how much it is worth asking for.

    That worked, and the prompt had never mentioned the block. It enumerated this
    deployment's shipped signal names in prose instead, so a newly configured
    field reached the model as an unannounced entry in an unnamed part of the
    context and was used by inference. Naming the block, and saying it wins over
    the enumerated names, is what makes the guarantee something the model was
    told rather than something it worked out.
    """
    task = load_ai_gateway_configuration(CONFIG).configuration.tasks["ORDER_AGENT_REASONING_V1"]
    assert "contextJson.identification_fields" in task.systemPrompt
    assert "intentKey" in task.systemPrompt, (
        "the prompt names the catalogue but not the key the model must populate "
        "from it, which is the half that turns it into an instruction"
    )

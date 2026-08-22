from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import SecretStr

from return_platform.ai.providers import ProviderError, ProviderResponse
from return_platform.ai.routing.routes import AIRoute, build_routes
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import ModelTier, load_ai_gateway_configuration
from return_platform.configuration.bootstrap_runtime_integrations import (
    _project_validated_pairs,
    _ValidatedPair,
)
from return_platform.configuration.return_configuration import (
    AIModelBindingConfiguration,
    AIProviderRuntimeConfiguration,
    AIValidatedRouteConfiguration,
    CredentialBindingConfiguration,
)
from return_platform.configuration.runtime_integrations import (
    apply_graph_runtime_configuration,
)
from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.integration.model_gateway import (
    RoutePoolReasoningModelGateway,
    StandardReasoningUnavailable,
)
from return_platform.dynamic_knowledge.order_agent.contracts import AgentTurnContext

CONFIG = Path(__file__).resolve().parents[1] / "config" / "ai_gateway.yaml"
CHECKSUM = "a" * 64


class FailingProvider:
    configured = True

    def __init__(self, name: str, model: str, calls: list[str]) -> None:
        self.name = name
        self.model = model
        self._calls = calls

    async def generate(self, request: object) -> ProviderResponse:
        del request
        self._calls.append(self.name)
        raise ProviderError("PROVIDER_UNAVAILABLE")


class HangingProvider:
    """Accepts the call and never answers in time.

    A MANUAL route is exactly this when nobody is at the console: the request is
    held for a human and the deadline arrives first.
    """

    configured = True

    def __init__(self, name: str, model: str, calls: list[str]) -> None:
        self.name = name
        self.model = model
        self._calls = calls

    async def generate(self, request: object) -> ProviderResponse:
        del request
        self._calls.append(self.name)
        await asyncio.sleep(30)
        raise AssertionError("the deadline should have fired first")


class SuccessfulProvider:
    configured = True

    def __init__(self, name: str, model: str, calls: list[str]) -> None:
        self.name = name
        self.model = model
        self._calls = calls

    async def generate(self, request: object) -> ProviderResponse:
        del request
        self._calls.append(self.name)
        return ProviderResponse(
            provider=self.name,
            model=self.model,
            text=(
                '{"business_capability":"order-discovery",'
                '"action_type":"OUT_OF_SCOPE",'
                '"decision_summary":"The request is outside the configured scope."}'
            ),
            input_tokens=10,
            output_tokens=8,
            total_tokens=18,
        )


def _route(
    *,
    provider: Any,
    provider_name: str,
    model: str,
    credential_id: str,
    provider_priority: int,
    model_priority: int,
    tier: ModelTier = ModelTier.STANDARD,
    allowed_task_keys: frozenset[str] = frozenset({"ORDER_AGENT_REASONING_V1"}),
) -> AIRoute:
    return AIRoute(
        route_id=f"{provider_name.lower()}/{model}/{credential_id}",
        provider_name=provider_name,
        model=model,
        credential_id=credential_id,
        credential_fingerprint="test",
        tier=tier,
        provider=provider,
        provider_priority=provider_priority,
        model_priority=model_priority,
        credential_priority=0,
        allowed_task_keys=allowed_task_keys,
    )


def test_sparse_validated_pairs_are_preserved_without_cartesian_intersection() -> None:
    pairs = [
        _ValidatedPair(1, "google-a", "STANDARD", ("reachable",)),
        _ValidatedPair(2, "google-b", "STANDARD", ("reachable",)),
    ]

    credentials, models = _project_validated_pairs(pairs)

    assert credentials == {1, 2}
    assert models == {("google-a", "STANDARD"), ("google-b", "STANDARD")}


def test_provider_configuration_accepts_sparse_validated_routes() -> None:
    provider = AIProviderRuntimeConfiguration(
        provider_key="GOOGLE",
        enabled=True,
        base_url="https://generativelanguage.googleapis.com/v1beta",
        credentials=(
            CredentialBindingConfiguration(
                profile_key="google-key-1",
                vault_reference="vault://secret/production/ai/google/key-1#api_key?version=1",
                validation_receipt_id="receipt-1",
                validation_configuration_checksum=CHECKSUM,
            ),
            CredentialBindingConfiguration(
                profile_key="google-key-2",
                vault_reference="vault://secret/production/ai/google/key-2#api_key?version=1",
                validation_receipt_id="receipt-2",
                validation_configuration_checksum=CHECKSUM,
            ),
        ),
        models=(
            AIModelBindingConfiguration(
                model_id="google-a",
                model_class="STANDARD",
                task_keys=("ORDER_AGENT_REASONING_V1",),
                priority=1,
            ),
            AIModelBindingConfiguration(
                model_id="google-b",
                model_class="STANDARD",
                task_keys=("ORDER_AGENT_REASONING_V1",),
                priority=2,
            ),
        ),
        validated_routes=(
            AIValidatedRouteConfiguration(
                credential_profile_key="google-key-1",
                model_id="google-a",
                task_key="ORDER_AGENT_REASONING_V1",
                validation_receipt_id="receipt-1",
                validation_configuration_checksum=CHECKSUM,
            ),
            AIValidatedRouteConfiguration(
                credential_profile_key="google-key-2",
                model_id="google-b",
                task_key="ORDER_AGENT_REASONING_V1",
                validation_receipt_id="receipt-2",
                validation_configuration_checksum=CHECKSUM,
            ),
        ),
        priority=1,
    )

    assert len(provider.validated_routes) == 2


def test_provider_configuration_accepts_unvalidated_configured_routes() -> None:
    provider = AIProviderRuntimeConfiguration(
        provider_key="GOOGLE",
        enabled=True,
        base_url="https://generativelanguage.googleapis.com/v1beta",
        credentials=(
            CredentialBindingConfiguration(
                profile_key="google-key-1",
                vault_reference=("vault://secret/production/ai/google/key-1#api_key?version=1"),
                bootstrap_managed=True,
            ),
        ),
        models=(
            AIModelBindingConfiguration(
                model_id="google-a",
                model_class="STANDARD",
                task_keys=("ORDER_AGENT_REASONING_V1",),
                priority=1,
            ),
        ),
        validated_routes=(),
        priority=1,
    )

    assert provider.enabled is True
    assert provider.validated_routes == ()


def test_graph_runtime_emits_pair_specific_route_bindings(
    test_settings: Settings,
) -> None:
    provider = AIProviderRuntimeConfiguration(
        provider_key="GOOGLE",
        enabled=True,
        base_url="https://generativelanguage.googleapis.com/v1beta",
        credentials=(
            CredentialBindingConfiguration(
                profile_key="google-key-1",
                vault_reference="vault://secret/production/ai/google/key-1#api_key?version=1",
                validation_receipt_id="receipt-1",
                validation_configuration_checksum=CHECKSUM,
            ),
            CredentialBindingConfiguration(
                profile_key="google-key-2",
                vault_reference="vault://secret/production/ai/google/key-2#api_key?version=1",
                validation_receipt_id="receipt-2",
                validation_configuration_checksum=CHECKSUM,
            ),
        ),
        models=(
            AIModelBindingConfiguration(
                model_id="google-a",
                model_class="STANDARD",
                task_keys=("ORDER_AGENT_REASONING_V1",),
                priority=1,
            ),
            AIModelBindingConfiguration(
                model_id="google-b",
                model_class="STANDARD",
                task_keys=("ORDER_AGENT_REASONING_V1",),
                priority=2,
            ),
        ),
        validated_routes=(
            AIValidatedRouteConfiguration(
                credential_profile_key="google-key-1",
                model_id="google-a",
                task_key="ORDER_AGENT_REASONING_V1",
                validation_receipt_id="receipt-1",
                validation_configuration_checksum=CHECKSUM,
            ),
            AIValidatedRouteConfiguration(
                credential_profile_key="google-key-2",
                model_id="google-b",
                task_key="ORDER_AGENT_REASONING_V1",
                validation_receipt_id="receipt-2",
                validation_configuration_checksum=CHECKSUM,
            ),
        ),
        priority=1,
    )
    configuration = SimpleNamespace(
        runtime_integrations=SimpleNamespace(
            ai_providers=(provider,),
            data_sources=(),
        )
    )

    updated = apply_graph_runtime_configuration(
        test_settings,
        cast(Any, configuration),
    )

    assert updated.ai_validated_route_bindings == (
        "GOOGLE|0|google-a|ORDER_AGENT_REASONING_V1",
        "GOOGLE|1|google-b|ORDER_AGENT_REASONING_V1",
    )


def test_build_routes_keeps_all_configured_pairs(
    test_settings: Settings,
) -> None:
    settings = test_settings.model_copy(
        update={
            "ai_provider_order": "GOOGLE",
            "google_api_keys": (
                SecretStr("key-1"),
                SecretStr("key-2"),
            ),
            "google_standard_models": ("google-a", "google-b"),
            "google_lightweight_models": (),
            "ai_validated_route_bindings": (
                "GOOGLE|0|google-a|ORDER_AGENT_REASONING_V1",
                "GOOGLE|1|google-b|ORDER_AGENT_REASONING_V1",
            ),
        }
    )

    routes = [route for route in build_routes(settings) if route.tier is ModelTier.STANDARD]

    assert {(route.credential_id, route.model) for route in routes} == {
        ("google-key-1", "google-a"),
        ("google-key-1", "google-b"),
        ("google-key-2", "google-a"),
        ("google-key-2", "google-b"),
    }


@pytest.mark.asyncio
async def test_candidates_are_provider_balanced_and_rotate_within_provider() -> None:
    loaded = load_ai_gateway_configuration(CONFIG)
    calls: list[str] = []
    routes = (
        _route(
            provider=SuccessfulProvider("GOOGLE", "google-a", calls),
            provider_name="GOOGLE",
            model="google-a",
            credential_id="google-key-1",
            provider_priority=0,
            model_priority=0,
        ),
        _route(
            provider=SuccessfulProvider("GOOGLE", "google-b", calls),
            provider_name="GOOGLE",
            model="google-b",
            credential_id="google-key-2",
            provider_priority=0,
            model_priority=1,
        ),
        _route(
            provider=SuccessfulProvider("NVIDIA", "nvidia-a", calls),
            provider_name="NVIDIA",
            model="nvidia-a",
            credential_id="nvidia-key-1",
            provider_priority=1,
            model_priority=0,
        ),
    )
    pool = AIRoutePool(routes, loaded.configuration)
    task = loaded.configuration.tasks["ORDER_AGENT_REASONING_V1"]

    first = await pool.candidates(task, task_id="ORDER_AGENT_REASONING_V1")
    second = await pool.candidates(task, task_id="ORDER_AGENT_REASONING_V1")

    assert [route.provider_name for route in first] == ["GOOGLE", "NVIDIA", "GOOGLE"]
    assert [route.model for route in first] == ["google-a", "nvidia-a", "google-b"]
    assert [route.model for route in second] == ["google-b", "nvidia-a", "google-a"]


@pytest.mark.asyncio
async def test_order_agent_fails_over_to_next_provider_and_logs_attempts(
    test_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    loaded = load_ai_gateway_configuration(CONFIG)
    calls: list[str] = []
    routes = (
        _route(
            provider=FailingProvider("GOOGLE", "google-a", calls),
            provider_name="GOOGLE",
            model="google-a",
            credential_id="google-key-1",
            provider_priority=0,
            model_priority=0,
        ),
        _route(
            provider=SuccessfulProvider("NVIDIA", "nvidia-a", calls),
            provider_name="NVIDIA",
            model="nvidia-a",
            credential_id="nvidia-key-1",
            provider_priority=1,
            model_priority=0,
        ),
    )
    gateway = RoutePoolReasoningModelGateway(
        settings=test_settings.model_copy(
            update={
                "ai_timeout_seconds": 1.0,
                "ai_global_timeout_seconds": 5.0,
            }
        ),
        configuration=loaded.configuration,
        route_pool=AIRoutePool(routes, loaded.configuration),
    )
    context = AgentTurnContext(
        conversation_id="conversation-1",
        client_turn_id="turn-1",
        agent_id="agent_a",
        user_message="Find order ORD-10001",
        as_of=datetime(2026, 8, 13, 9, 30, tzinfo=UTC),
        session_timezone="UTC",
        schema_version="1",
        graph_generation_id="generation-1",
        configuration_release_id="release-1",
        policy_version="policy-1",
        prompt_version="prompt-1",
        compact_schema={},
        conversation_state={},
    )

    with caplog.at_level(
        logging.INFO,
        logger="return_platform.dynamic_knowledge.model_gateway",
    ):
        result = await gateway.decide(context)

    assert calls == ["GOOGLE", "NVIDIA"]
    assert result.provider == "NVIDIA"
    # Prefix match, not equality: the failure line carries %-formatted detail
    # (attempt, tier, provider, model, error, latency) and `caplog.messages`
    # holds the *rendered* message, so an equality check could never match it.
    # It never did -- this assertion had been failing since it was written.
    assert any(
        message.startswith("order_agent_model_attempt_failed") for message in caplog.messages
    ), caplog.messages
    assert "order_agent_model_attempt_succeeded" in caplog.messages


@pytest.mark.asyncio
async def test_order_agent_escalates_to_lightweight_tier_when_standard_exhausted(
    test_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    loaded = load_ai_gateway_configuration(CONFIG)
    task = loaded.configuration.tasks["ORDER_AGENT_REASONING_V1"]
    assert task.allowTierEscalation is True, "test assumes escalation is enabled in config"

    calls: list[str] = []
    routes = (
        _route(
            provider=FailingProvider("GOOGLE", "google-standard", calls),
            provider_name="GOOGLE",
            model="google-standard",
            credential_id="google-key-1",
            provider_priority=0,
            model_priority=0,
            tier=ModelTier.STANDARD,
        ),
        _route(
            provider=SuccessfulProvider("GOOGLE", "google-lite", calls),
            provider_name="GOOGLE",
            model="google-lite",
            credential_id="google-key-1",
            provider_priority=0,
            model_priority=0,
            tier=ModelTier.LIGHTWEIGHT,
        ),
    )
    gateway = RoutePoolReasoningModelGateway(
        settings=test_settings.model_copy(
            update={
                "ai_timeout_seconds": 1.0,
                "ai_global_timeout_seconds": 5.0,
            }
        ),
        configuration=loaded.configuration,
        route_pool=AIRoutePool(routes, loaded.configuration),
    )
    context = AgentTurnContext(
        conversation_id="conversation-1",
        client_turn_id="turn-1",
        agent_id="agent_a",
        user_message="Find order ORD-10001",
        as_of=datetime(2026, 8, 13, 9, 30, tzinfo=UTC),
        session_timezone="UTC",
        schema_version="1",
        graph_generation_id="generation-1",
        configuration_release_id="release-1",
        policy_version="policy-1",
        prompt_version="prompt-1",
        compact_schema={},
        conversation_state={},
    )

    with caplog.at_level(
        logging.INFO,
        logger="return_platform.dynamic_knowledge.model_gateway",
    ):
        result = await gateway.decide(context)

    assert calls == ["GOOGLE", "GOOGLE"]
    assert result.provider == "GOOGLE"
    assert result.model == "google-lite"
    assert "order_agent_tier_escalation_started" in caplog.messages
    assert "order_agent_model_attempt_succeeded" in caplog.messages


@pytest.mark.asyncio
async def test_a_timed_out_route_is_not_re_queued_by_the_tier_escalation(
    test_settings: Settings,
) -> None:
    """One provider offered at both tiers must not be asked twice for silence.

    `route_id` is provider/model/credential and the tier loop sits outside it,
    so a provider offering one model at both tiers -- which MANUAL does,
    unconditionally -- yields two routes with one identity. Escalating onto the
    second paid the full per-attempt timeout again against a respondent that had
    just failed to answer the identical question.

    That is how one Order Discovery turn reached roughly fourteen minutes: three
    serial 280-second waits, on one provider, with nothing about the second or
    third ask different from the first. The associate saw "Searching order
    graph..." for nine and a half minutes and then an error.

    Only timeouts are suppressed. A *rejected* answer still escalates onto the
    same route, because on a keyless deployment that second hold is how the
    operator is told what was wrong with the first answer --
    `test_keyless_reasoning_is_held_for_a_human` holds that shut.
    """
    loaded = load_ai_gateway_configuration(CONFIG)
    calls: list[str] = []
    # One identity, two tiers: same provider, same model, same credential.
    routes = tuple(
        _route(
            provider=HangingProvider("MANUAL", "manual-human-v1", calls),
            provider_name="MANUAL",
            model="manual-human-v1",
            credential_id="manual-local",
            provider_priority=0,
            model_priority=0,
            tier=tier,
        )
        for tier in (ModelTier.STANDARD, ModelTier.LIGHTWEIGHT)
    )
    assert routes[0].route_id == routes[1].route_id, "the test needs one identity"

    gateway = RoutePoolReasoningModelGateway(
        settings=test_settings.model_copy(
            update={"ai_timeout_seconds": 0.2, "ai_global_timeout_seconds": 5.0}
        ),
        configuration=loaded.configuration,
        route_pool=AIRoutePool(routes, loaded.configuration),
    )
    context = AgentTurnContext(
        conversation_id="conversation-1",
        client_turn_id="turn-1",
        agent_id="agent_a",
        user_message="Find order ORD-10001",
        as_of=datetime(2026, 8, 13, 9, 30, tzinfo=UTC),
        session_timezone="UTC",
        schema_version="1",
        graph_generation_id="generation-1",
        configuration_release_id="release-1",
        policy_version="policy-1",
        prompt_version="prompt-1",
        compact_schema={},
        conversation_state={},
    )

    with pytest.raises(StandardReasoningUnavailable):
        await gateway.decide(context)

    # The route is attempted under its own tier and not re-queued under the
    # other. `maximumAttemptsPerRoute` may allow more than one try of the same
    # route within a pass; what must not happen is the escalation adding a
    # second pass over the identity that just timed out.
    assert len(calls) <= loaded.configuration.retry.maximumAttemptsPerRoute, (
        f"the timed-out identity was re-queued by the escalation: {len(calls)} calls"
    )

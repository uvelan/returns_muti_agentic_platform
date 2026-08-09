"""The real reasoning path, end to end, with a human-authored model response.

Every prior slice recorded the same gap: "no full LLM-driven end-to-end test",
because the order agent's entry node always calls the model gateway and the
provider API keys were rotated out. That framing was wrong in an important way —
**the platform already ships a provider for exactly this**. `ManualFileProvider`
writes the request a real model would have received to `.manual_llm/requests/`
and waits for a human-authored reply in `.manual_llm/responses/`, and
`ORDER_AGENT_REASONING_V1` already lists `MANUAL` among its `allowedProviders`.

So this exercises the genuine production path — `build_routes` →
`AIRoutePool.candidates` → tier/provider gating → rate-limit acquisition →
`ManualFileProvider.generate` → response-schema parse → typed `AgentAction` —
with no external API call and no real credential. The only thing standing in for
a model is the JSON a human would type, supplied here by a background task.

What this does *not* cover: real model behaviour. It proves the pipeline carries
a well-formed answer and rejects a malformed one, not that any model would
produce the former.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from return_platform.ai_gateway.configuration import load_ai_gateway_configuration
from return_platform.ai_gateway.providers import manual as manual_provider
from return_platform.ai_gateway.routing import AIRoutePool, build_routes
from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.integration.model_gateway import (
    RoutePoolReasoningModelGateway,
    StandardReasoningUnavailable,
)
from return_platform.dynamic_knowledge.order_agent.contracts import (
    ActionType,
    AgentTurnContext,
)

_TASK_ID = "ORDER_AGENT_REASONING_V1"


def _settings() -> Settings:
    """A MANUAL-only provider order.

    `environment="test"` is what gates ManualFileProvider on; settings refuse
    MANUAL in production, so this configuration is structurally impossible to
    deploy.
    """
    return Settings.model_construct(
        environment="test",
        ai_provider_order="MANUAL",
        ai_max_payload_bytes=1_000_000,
        ai_global_timeout_seconds=30.0,
        ai_timeout_seconds=30.0,
    )


@pytest.fixture
def manual_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the provider's handoff directory into the test's tmp_path.

    `routing.py` constructs `ManualFileProvider(settings)` with no `base_dir`, so
    the directory is always the module default resolved against the process CWD
    -- there is no setting for it. Patching the module global is therefore the
    only seam, and it has to happen before `build_routes` constructs the
    provider. (Worth knowing operationally: a real MANUAL session writes to
    `.manual_llm/` beside wherever the process was started.)
    """
    monkeypatch.setattr(manual_provider, "DEFAULT_MANUAL_LLM_DIR", tmp_path)
    return tmp_path


def _context() -> AgentTurnContext:
    return AgentTurnContext(
        conversation_id="conv-manual-1",
        client_turn_id="turn-1",
        user_message="Find order ORD-10001",
        schema_version="v1",
        graph_generation_id="generation-1",
        configuration_release_id="release-1",
        policy_version="p1",
        prompt_version="pv1",
        compact_schema={"entityIds": ["order"]},
        conversation_state={},
    )


def _valid_action_json() -> str:
    """What a well-behaved model would return for this turn."""
    return json.dumps(
        {
            "business_capability": "order-discovery",
            "action_type": ActionType.CLARIFY.value,
            "decision_summary": "Need the order number confirmed before searching.",
            "response": {
                "status": "NEEDS_CLARIFICATION",
                "business_capability": "order-discovery",
                "statements": [],
                "suggestions": [],
                "requested_input": "Which order number are you asking about?",
            },
        }
    )


async def _respond_to_first_request(
    base_dir: Path, body: str, *, wait_seconds: float = 20.0
) -> dict:
    """Stand in for the human at `scripts/manual_llm_responder.py`.

    Polls for the request file the provider writes, captures it so the test can
    assert on what the model would actually have seen, then writes the reply
    under the matching request id.
    """
    requests_dir = base_dir / "requests"
    responses_dir = base_dir / "responses"
    deadline = asyncio.get_running_loop().time() + wait_seconds
    while asyncio.get_running_loop().time() < deadline:
        if requests_dir.is_dir():
            pending = sorted(requests_dir.glob("*.json"))
            if pending:
                request_path = pending[0]
                try:
                    payload = json.loads(request_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    # The provider may be mid-write; try again next tick.
                    await asyncio.sleep(0.05)
                    continue
                responses_dir.mkdir(parents=True, exist_ok=True)
                (responses_dir / f"{payload['requestId']}.json").write_text(body, encoding="utf-8")
                return payload
        await asyncio.sleep(0.05)
    raise AssertionError("the provider never wrote a request file")


def _gateway(settings: Settings) -> RoutePoolReasoningModelGateway:
    loaded = load_ai_gateway_configuration(settings.ai_gateway_configuration_path)
    configuration = loaded.configuration
    pool = AIRoutePool(build_routes(settings), configuration)
    return RoutePoolReasoningModelGateway(
        settings=settings, configuration=configuration, route_pool=pool, task_id=_TASK_ID
    )


@pytest.mark.asyncio
async def test_manual_provider_serves_a_real_reasoning_turn(manual_dir: Path) -> None:
    settings = _settings()
    gateway = _gateway(settings)

    responder = asyncio.create_task(_respond_to_first_request(manual_dir, _valid_action_json()))
    invocation = await gateway.decide(_context())
    request_payload = await responder

    assert invocation.provider == "MANUAL"
    assert invocation.action.action_type is ActionType.CLARIFY
    assert invocation.action.response is not None
    assert invocation.action.response.requested_input == (
        "Which order number are you asking about?"
    )

    # The request a real model would have received is well-formed and framed.
    assert request_payload["userPayload"]["mode"] == "DECIDE"
    assert "REQUIRED RESPONSE SCHEMA" in request_payload["systemPrompt"]
    context_json = json.loads(request_payload["userPayload"]["contextJson"])
    assert context_json["conversation_id"] == "conv-manual-1"


@pytest.mark.asyncio
async def test_the_turn_context_reaches_the_model_intact(manual_dir: Path) -> None:
    """What the model sees is the real serialized AgentTurnContext, not a
    summary -- so a prompt-shaping bug would surface here rather than as a
    quality regression nobody can attribute."""
    settings = _settings()
    gateway = _gateway(settings)

    responder = asyncio.create_task(_respond_to_first_request(manual_dir, _valid_action_json()))
    await gateway.decide(_context())
    payload = await responder

    context_json = json.loads(payload["userPayload"]["contextJson"])
    assert context_json["user_message"] == "Find order ORD-10001"
    assert context_json["graph_generation_id"] == "generation-1"
    # Nothing that would let source content pose as an instruction.
    assert payload["userPayload"]["invalidActionJson"] == ""


async def _respond_to_every_request(base_dir: Path, body: str) -> None:
    """Answer every request until cancelled.

    The retry path needs this: a rejected reply makes the gateway re-request, and
    a responder that answers only the first one leaves each retry waiting out the
    provider timeout. Answering all of them lets the gateway exhaust its attempt
    budget promptly, which is what the test is actually about.
    """
    requests_dir = base_dir / "requests"
    responses_dir = base_dir / "responses"
    answered: set[str] = set()
    while True:
        if requests_dir.is_dir():
            for request_path in sorted(requests_dir.glob("*.json")):
                try:
                    payload = json.loads(request_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                request_id = payload["requestId"]
                if request_id in answered:
                    continue
                responses_dir.mkdir(parents=True, exist_ok=True)
                (responses_dir / f"{request_id}.json").write_text(body, encoding="utf-8")
                answered.add(request_id)
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_a_malformed_model_reply_is_rejected_not_coerced(manual_dir: Path) -> None:
    """A reply that is not a valid AgentAction must fail the turn rather than be
    partially interpreted -- the strict-response contract is what keeps model
    output from becoming untyped state."""
    settings = _settings()
    gateway = _gateway(settings)

    responder = asyncio.create_task(
        _respond_to_every_request(manual_dir, '{"not": "an AgentAction"}')
    )
    try:
        with pytest.raises(StandardReasoningUnavailable):
            await gateway.decide(_context())
    finally:
        responder.cancel()


@pytest.mark.asyncio
async def test_manual_is_unavailable_outside_development_and_test(tmp_path: Path) -> None:
    """The gate that makes this whole mechanism safe to ship."""
    from return_platform.ai_gateway.providers.contracts import ProviderError, ProviderRequest
    from return_platform.ai_gateway.providers.manual import ManualFileProvider

    provider = ManualFileProvider(
        Settings.model_construct(environment="production"), base_dir=tmp_path
    )
    assert provider.configured is False
    with pytest.raises(ProviderError):
        await provider.generate(
            ProviderRequest(
                system_prompt="p",
                user_payload={},
                temperature=0.0,
                max_output_tokens=16,
            )
        )


def test_the_order_agent_task_permits_the_manual_provider() -> None:
    """If MANUAL is ever removed from this task's allowlist, every test above
    starts skipping the real routing path silently -- so assert it directly."""
    settings = Settings.model_construct(environment="test")
    configuration = load_ai_gateway_configuration(
        settings.ai_gateway_configuration_path
    ).configuration
    task: Any = configuration.tasks[_TASK_ID]
    assert "MANUAL" in task.allowedProviders

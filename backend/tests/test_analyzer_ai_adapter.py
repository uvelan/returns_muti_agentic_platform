"""The analyzer's `SchemaReasoningPort` binding, over the shared AI path.

The failover test here is deliberately not a duplicate of
`test_ai_route_balancing_design.py`'s: that one proves the machinery works, this
one proves the *analyzer* is actually running on that machinery rather than on a
simpler private copy. If someone gives the adapter its own invocation loop, this
is the test that fails.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from return_platform.ai.providers import ProviderError, ProviderRequest, ProviderResponse
from return_platform.ai.routing.routes import AIRoute
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import ModelTier, load_ai_gateway_configuration
from return_platform.bootstrap.adapters.analyzer_ai_adapter import (
    GRAPH_SCHEMA_PROPOSAL_TASK_ID,
    SchemaProposalUnavailable,
    build_analyzer_ai_adapter,
)
from return_platform.configuration.settings import Settings
from return_platform.graph_schema_analyzer.application.prompt_context import (
    PromptBlockKind,
    build_prompt_blocks,
)
from return_platform.graph_schema_analyzer.ports.ai_port import SchemaReasoningPort

CONFIG = Path(__file__).resolve().parents[1] / "config" / "ai_gateway.yaml"

_PROPOSAL_JSON = json.dumps(
    {
        "nodes": [
            {
                "label": "Customer",
                "properties": [{"name": "customer_id", "type": "string"}],
                "source_dataset": "customers",
                "rationale": "customers.customer_id is unique across the sample.",
            }
        ],
        "relationships": [
            {
                "relationship_type": "PLACED",
                "from_label": "Customer",
                "to_label": "Order",
                "rationale": "orders.customer_id matches customers.customer_id.",
            }
        ],
        "open_questions": ["Is orders.status a closed enumeration?"],
    }
)


class _RecordingProvider:
    """Captures the request so the prompt framing can be asserted on."""

    configured = True

    def __init__(self, name: str, model: str, calls: list[str], text: str = _PROPOSAL_JSON) -> None:
        self.name = name
        self.model = model
        self._calls = calls
        self._text = text
        self.requests: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self._calls.append(self.name)
        self.requests.append(request)
        return ProviderResponse(
            provider=self.name,
            model=self.model,
            text=self._text,
            input_tokens=20,
            output_tokens=30,
            total_tokens=50,
        )


class _FailingProvider:
    configured = True

    def __init__(self, name: str, model: str, calls: list[str]) -> None:
        self.name = name
        self.model = model
        self._calls = calls

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        del request
        self._calls.append(self.name)
        raise ProviderError("PROVIDER_UNAVAILABLE")


def _route(
    *,
    provider: Any,
    provider_name: str,
    model: str,
    credential_id: str,
    provider_priority: int,
    tier: ModelTier = ModelTier.STANDARD,
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
        model_priority=0,
        credential_priority=0,
        allowed_task_keys=frozenset({GRAPH_SCHEMA_PROPOSAL_TASK_ID}),
    )


def _blocks() -> list[dict[str, Any]]:
    built = build_prompt_blocks(
        task_definition="Propose a graph schema for the discovered datasets.",
        source_metadata=[
            {
                "source_id": "return_source",
                "dataset_name": "customers",
                "fields": [{"field_name": "customer_id", "declared_type": "string"}],
            }
        ],
        untrusted_samples=None,
        user_requirements="Model customers and their orders.",
    )
    return [block.model_dump(mode="json") for block in built]


def _adapter(settings: Settings, routes: tuple[AIRoute, ...]) -> SchemaReasoningPort:
    loaded = load_ai_gateway_configuration(CONFIG)
    return build_analyzer_ai_adapter(
        settings=settings.model_copy(
            update={"ai_timeout_seconds": 1.0, "ai_global_timeout_seconds": 5.0}
        ),
        configuration=loaded.configuration,
        route_pool=AIRoutePool(routes, loaded.configuration),
    )


def test_factory_returns_something_satisfying_the_port(test_settings: Settings) -> None:
    calls: list[str] = []
    adapter = _adapter(
        test_settings,
        (
            _route(
                provider=_RecordingProvider("GOOGLE", "google-a", calls),
                provider_name="GOOGLE",
                model="google-a",
                credential_id="google-key-1",
                provider_priority=0,
            ),
        ),
    )

    # The typed factory is what makes mypy prove conformance; this only guards
    # the runtime half the analyzer's own DI guard relies on.
    assert isinstance(adapter, SchemaReasoningPort)


@pytest.mark.asyncio
async def test_proposal_is_parsed_and_grounded_in_the_callers_snapshot_hash(
    test_settings: Settings,
) -> None:
    calls: list[str] = []
    provider = _RecordingProvider("GOOGLE", "google-a", calls)
    adapter = _adapter(
        test_settings,
        (
            _route(
                provider=provider,
                provider_name="GOOGLE",
                model="google-a",
                credential_id="google-key-1",
                provider_priority=0,
            ),
        ),
    )

    proposal = await adapter.propose_schema(
        analysis_id="analysis-1",
        snapshot_content_hash="b" * 64,
        prompt_blocks=_blocks(),
    )

    assert calls == ["GOOGLE"]
    # The model never supplied a hash -- the adapter stamps the authoritative one.
    assert proposal.snapshot_content_hash == "b" * 64
    assert [node.label for node in proposal.nodes] == ["Customer"]
    assert proposal.nodes[0].source_dataset == "customers"
    assert [edge.relationship_type for edge in proposal.relationships] == ["PLACED"]
    assert proposal.open_questions == ("Is orders.status a closed enumeration?",)


@pytest.mark.asyncio
async def test_the_model_is_never_asked_for_the_snapshot_hash(
    test_settings: Settings,
) -> None:
    """A field the model cannot know should not be in the schema it is shown."""
    calls: list[str] = []
    provider = _RecordingProvider("GOOGLE", "google-a", calls)
    adapter = _adapter(
        test_settings,
        (
            _route(
                provider=provider,
                provider_name="GOOGLE",
                model="google-a",
                credential_id="google-key-1",
                provider_priority=0,
            ),
        ),
    )

    await adapter.propose_schema(
        analysis_id="analysis-1",
        snapshot_content_hash="c" * 64,
        prompt_blocks=_blocks(),
    )

    request = provider.requests[0]
    assert "snapshot_content_hash" not in json.dumps(request.response_schema)
    assert "snapshot_content_hash" not in request.system_prompt


@pytest.mark.asyncio
async def test_six_block_framing_reaches_the_provider_unaltered(
    test_settings: Settings,
) -> None:
    """The adapter passes the analyzer's blocks through; it does not re-frame."""
    calls: list[str] = []
    provider = _RecordingProvider("GOOGLE", "google-a", calls)
    adapter = _adapter(
        test_settings,
        (
            _route(
                provider=provider,
                provider_name="GOOGLE",
                model="google-a",
                credential_id="google-key-1",
                provider_priority=0,
            ),
        ),
    )
    blocks = _blocks()

    await adapter.propose_schema(
        analysis_id="analysis-1",
        snapshot_content_hash="d" * 64,
        prompt_blocks=blocks,
    )

    payload = provider.requests[0].user_payload
    assert payload["analysisId"] == "analysis-1"
    assert json.loads(payload["promptBlocksJson"]) == blocks
    # All six blocks, in order, with block 5 still marked untrusted.
    kinds = [block["kind"] for block in blocks]
    assert kinds == [kind.value for kind in PromptBlockKind]
    assert blocks[4]["trusted"] is False


@pytest.mark.asyncio
async def test_analyzer_proposals_fail_over_to_the_next_provider(
    test_settings: Settings,
) -> None:
    """Proves the adapter runs on the shared route pool, not a private loop."""
    calls: list[str] = []
    adapter = _adapter(
        test_settings,
        (
            _route(
                provider=_FailingProvider("GOOGLE", "google-a", calls),
                provider_name="GOOGLE",
                model="google-a",
                credential_id="google-key-1",
                provider_priority=0,
            ),
            _route(
                provider=_RecordingProvider("NVIDIA", "nvidia-a", calls),
                provider_name="NVIDIA",
                model="nvidia-a",
                credential_id="nvidia-key-1",
                provider_priority=1,
            ),
        ),
    )

    proposal = await adapter.propose_schema(
        analysis_id="analysis-1",
        snapshot_content_hash="e" * 64,
        prompt_blocks=_blocks(),
    )

    assert calls == ["GOOGLE", "NVIDIA"]
    assert proposal.snapshot_content_hash == "e" * 64


@pytest.mark.asyncio
async def test_every_route_failing_raises_rather_than_returning_a_guess(
    test_settings: Settings,
) -> None:
    calls: list[str] = []
    adapter = _adapter(
        test_settings,
        (
            _route(
                provider=_FailingProvider("GOOGLE", "google-a", calls),
                provider_name="GOOGLE",
                model="google-a",
                credential_id="google-key-1",
                provider_priority=0,
            ),
        ),
    )

    with pytest.raises(SchemaProposalUnavailable):
        await adapter.propose_schema(
            analysis_id="analysis-1",
            snapshot_content_hash="f" * 64,
            prompt_blocks=_blocks(),
        )

"""One prompt per conversation stage, and the fallback that lets it ship.

The reasoning prompt reached 17,109 characters and adherence at that size is
visibly poor: an associate sent an explicit "confirm the customer X on account
Y" and the agent asked which location, twice, with the rule against exactly that
present, correct and delivered. Splitting the prompt by conversation stage sends
fewer rules per turn.

Two things have to hold for that to be safe, and both are asserted here.

*Nothing is lost.* Every stage prompt is a subset of `ORDER_AGENT_REASONING_V1`,
section for section and byte for byte -- they are YAML aliases to one set of
anchors, so a stage cannot drift from the complete prompt -- and every stage
carries the core that no turn can do without.

*Nothing breaks on an old release.* `runtime-configuration-init` in compose.yaml
still publishes with `--if-missing`, so a container deployment can be running a
release cut before these task ids existed. A stage task that is absent, or that
no route is allowed to serve, falls back to the complete prompt rather than
raising -- which means the change degrades to today's behaviour instead of
failing to start.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from return_platform.ai.providers import ProviderRequest, ProviderResponse
from return_platform.ai.routing.routes import AIRoute
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import (
    AIGatewayConfiguration,
    ModelTier,
    load_ai_gateway_configuration,
)
from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.integration.model_gateway import (
    RoutePoolReasoningModelGateway,
)
from return_platform.dynamic_knowledge.order_agent.contracts import AgentTurnContext
from return_platform.dynamic_knowledge.order_agent.reasoning_stage import (
    BASE_REASONING_TASK_ID,
    STAGE_TASK_IDS,
    ReasoningStage,
    reasoning_stage,
    stage_task_id,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
CONFIG = BACKEND_ROOT / "config" / "ai_gateway.yaml"

#: Sections no stage may drop. The role and untrusted-input framing decides how
#: everything else is read; the payload contract and statement rules are what
#: makes a response parseable at all; the fact rules are how a turn is
#: remembered, and a turn that forgets is the defect that produced the
#: re-asking in the first place.
CORE_SECTIONS = frozenset(
    {
        "role-and-untrusted-input",
        "action-payload-contract",
        "statement-and-identifier-rules",
        "reporting-observed-facts",
        "naming-a-fact",
        "not-asking-twice",
        "honouring-a-confirmation",
        "evidence-and-scope",
        "voice",
        "reading-the-transcript",
    }
)


@pytest.fixture(scope="module")
def configuration() -> AIGatewayConfiguration:
    return load_ai_gateway_configuration(CONFIG).configuration


# --- the classification -------------------------------------------------------


@pytest.mark.parametrize(
    ("case_id", "conversation_state", "expected"),
    [
        (None, {}, ReasoningStage.OPENING),
        # REPLAN sets `order_search_cache` to None, which is the same situation
        # as never having searched and must read as such.
        (None, {"orderSearchCache": None}, ReasoningStage.OPENING),
        (None, {"orderSearchCache": {"totalFound": 0, "shown": 0}}, ReasoningStage.UNRESOLVED),
        (None, {"orderSearchCache": {"totalFound": 1, "shown": 1}}, ReasoningStage.COMPLETING),
        (None, {"orderSearchCache": {"totalFound": 4, "shown": 4}}, ReasoningStage.NARROWING),
        (
            None,
            {"orderSearchCache": {"totalFound": 40, "shown": 5}},
            ReasoningStage.NARROWING_TRUNCATED,
        ),
        # A confirmed order outranks whatever the search cache still holds: the
        # associate has an order and the turn is about what is coming off it.
        ("case-1", {"orderSearchCache": {"totalFound": 40, "shown": 5}}, ReasoningStage.COMPLETING),
    ],
)
def test_the_stage_is_read_off_the_turns_own_state(
    case_id: str | None, conversation_state: dict[str, Any], expected: ReasoningStage
) -> None:
    """Every signal is one the graph itself writes.

    `order_search_cache` comes from `make_order_search_node`, `case_id` from
    `make_confirm_order_node`. Nothing here reads the associate's message:
    classifying on untrusted text would put it in charge of which rules apply to
    it.
    """
    assert reasoning_stage(case_id=case_id, conversation_state=conversation_state) is expected


@pytest.mark.parametrize(
    "conversation_state",
    [
        {"orderSearchCache": "not-a-mapping"},
        {"orderSearchCache": {"shown": 5}},
        {"orderSearchCache": {"totalFound": "many"}},
        # `True` is an `int` in Python and would otherwise classify as
        # COMPLETING, which is the wrong prompt for a shape nobody wrote.
        {"orderSearchCache": {"totalFound": True}},
    ],
)
def test_a_cache_shape_it_cannot_read_asks_for_the_complete_prompt(
    conversation_state: dict[str, Any],
) -> None:
    """A conversation document written by a future release must not be guessed at.

    `None` is a real answer, not an error: the caller sends the complete prompt,
    which is always correct and merely larger.
    """
    assert reasoning_stage(case_id=None, conversation_state=conversation_state) is None
    assert stage_task_id(None) == BASE_REASONING_TASK_ID


# --- the stage prompts are subsets, and they keep the core --------------------


def test_every_stage_prompt_is_a_byte_identical_subset_of_the_complete_one(
    configuration: AIGatewayConfiguration,
) -> None:
    """Aliases, not copies.

    The YAML anchors each section once in `ORDER_AGENT_REASONING_V1` and the
    stage tasks reference them, so there is exactly one copy of every rule.
    Asserted rather than assumed because the alias is invisible after
    `yaml.safe_load` -- a future edit that pasted the text instead would load
    identically today and drift silently tomorrow.
    """
    base = configuration.tasks[BASE_REASONING_TASK_ID]
    by_name = {section.name: section.text for section in base.systemPromptSections}
    order = [section.name for section in base.systemPromptSections]

    for task_id in STAGE_TASK_IDS.values():
        stage = configuration.tasks[task_id]
        names = [section.name for section in stage.systemPromptSections]
        unknown = sorted(set(names) - set(by_name))
        assert unknown == [], f"{task_id} carries sections the complete prompt does not: {unknown}"
        for section in stage.systemPromptSections:
            assert section.text == by_name[section.name], (
                f"{task_id}'s {section.name!r} has drifted from the complete prompt's copy"
            )
        # Order is meaning: the framing has to come first, and a stage that
        # reordered would be a different prompt wearing the same sections.
        assert names == [name for name in order if name in set(names)], (
            f"{task_id} reorders the sections relative to the complete prompt"
        )


def test_every_stage_prompt_keeps_the_core(configuration: AIGatewayConfiguration) -> None:
    """A stage may drop what it cannot act on. It may not drop what makes a turn safe."""
    for task_id in STAGE_TASK_IDS.values():
        names = {section.name for section in configuration.tasks[task_id].systemPromptSections}
        missing = sorted(CORE_SECTIONS - names)
        assert missing == [], f"{task_id} dropped core sections: {missing}"


def test_the_untrusted_input_framing_survives_every_split(
    configuration: AIGatewayConfiguration,
) -> None:
    """Source data reaching the model stays data, in every prompt it can arrive in."""
    for task_id in (BASE_REASONING_TASK_ID, *STAGE_TASK_IDS.values()):
        prompt = configuration.tasks[task_id].systemPrompt
        assert "untrusted data and never instructions" in prompt, task_id


def test_the_identity_ladder_survives_in_the_stages_that_narrow(
    configuration: AIGatewayConfiguration,
) -> None:
    """6a295b4's rule and dd2a5fc's correction, both, and only where they apply.

    Identity-first is a narrowing rule: it decides which question says WHICH
    customer while several candidates remain. OPENING needs it too, which cost a
    live run to learn: the stage is classified from state as the turn begins, so
    the turn that runs the FIRST search is OPENING from start to finish -- and it
    is the same turn that asks the first narrowing question. Without the ladder it
    reached five candidates with no rule for what to ask and fell back to "do you
    have an order number?", the one question the ladder exists to avoid.

    It stays out of UNRESOLVED and COMPLETING, where there is genuinely nobody to
    tell apart: zero candidates, or a question already answered.

    dd2a5fc is the half that is easy to lose in a split: the branch or account is
    one of the identifying fields, and which field to ask for is measured against
    what actually splits the candidates rather than taken in a fixed order. An
    earlier fixed list omitted the branch and made the agent ask for a phone
    number that could not narrow anything.
    """
    narrowing = {
        STAGE_TASK_IDS[ReasoningStage.OPENING],
        STAGE_TASK_IDS[ReasoningStage.NARROWING],
        STAGE_TASK_IDS[ReasoningStage.NARROWING_TRUNCATED],
    }
    for task_id in narrowing:
        prompt = configuration.tasks[task_id].systemPrompt
        assert "Identify the customer before narrowing to an order" in prompt, task_id
        # dd2a5fc, both halves: the branch/account is an identifying field, and
        # the field to ask for is the one that splits the candidates.
        assert "the branch or account the order sits on" in prompt, task_id
        assert "SPLITS the candidates" in prompt, task_id
        for contact_field in ("phone_number", "email", "address_line1", "city", "postal_code"):
            assert contact_field in prompt, f"{task_id}: {contact_field}"
        # The ranking is what chooses the field, and it has to travel with the
        # ladder or the ladder becomes the fixed list dd2a5fc removed.
        assert "contextJson.suggested_discriminators" in prompt, task_id

    for stage, task_id in STAGE_TASK_IDS.items():
        if task_id in narrowing:
            continue
        names = {section.name for section in configuration.tasks[task_id].systemPromptSections}
        assert "identity-before-order" not in names, (
            f"{stage.value} carries the identity ladder but has nothing to narrow"
        )


def test_the_two_truncation_rules_ride_only_with_a_truncated_page(
    configuration: AIGatewayConfiguration,
) -> None:
    """Aggregates and cache paging are what `shown < totalFound` unlocks.

    Both are meaningless when the associate is already looking at every match,
    and together they are 2,149 characters -- the difference between the
    narrowing prompt and the widest one.
    """
    wide = {
        section.name
        for section in configuration.tasks[
            STAGE_TASK_IDS[ReasoningStage.NARROWING_TRUNCATED]
        ].systemPromptSections
    }
    narrow = {
        section.name
        for section in configuration.tasks[
            STAGE_TASK_IDS[ReasoningStage.NARROWING]
        ].systemPromptSections
    }
    assert wide - narrow == {"measuring-with-aggregates", "paging-the-cached-search"}
    assert narrow - wide == set()


def test_a_stage_prompt_is_meaningfully_smaller_than_the_complete_one(
    configuration: AIGatewayConfiguration,
) -> None:
    """The whole point. Liveness for the split, and a tripwire on it growing back.

    Not an arbitrary threshold: the complete prompt is what a turn used to carry
    unconditionally, and a stage that has crept back to within a tenth of it has
    stopped buying anything and should be re-examined rather than quietly kept.
    """
    complete = len(configuration.tasks[BASE_REASONING_TASK_ID].systemPrompt)
    for task_id in STAGE_TASK_IDS.values():
        length = len(configuration.tasks[task_id].systemPrompt)
        assert length < complete * 0.9, (
            f"{task_id} is {length} of the complete prompt's {complete} characters, "
            "close enough that the split is no longer paying for itself"
        )


# --- the fallback -------------------------------------------------------------


class _CapturingProvider:
    configured = True

    def __init__(self, model: str, text: str) -> None:
        self.model = model
        self._text = text
        self.requests: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        return ProviderResponse(
            provider="GOOGLE",
            model=self.model,
            text=self._text,
            input_tokens=100,
            cached_input_tokens=None,
            output_tokens=20,
            total_tokens=120,
        )


_ACTION = json.dumps(
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


def _settings() -> Settings:
    return Settings.model_construct(
        environment="test",
        ai_gateway_configuration_path=CONFIG,
        ai_timeout_seconds=2.0,
        ai_global_timeout_seconds=10.0,
        ai_max_payload_bytes=64_000,
        ai_provider_order="GOOGLE",
        ai_requests_per_minute=120,
    )


def _route(provider: Any, *, allowed_task_keys: frozenset[str] = frozenset()) -> AIRoute:
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
        allowed_task_keys=allowed_task_keys,
    )


def _context(conversation_state: dict[str, Any]) -> AgentTurnContext:
    return AgentTurnContext(
        conversation_id="conv-1",
        client_turn_id="turn-1",
        agent_id="order-discovery-agent",
        user_message="canary-utterance",
        as_of=datetime(2026, 8, 20, 9, 30, tzinfo=UTC),
        session_timezone="America/Chicago",
        schema_version="v1",
        graph_generation_id="generation-1",
        configuration_release_id="release-1",
        policy_version="p1",
        prompt_version="dynamic-order-agent-reasoning-v18",
        compact_schema={"entities": {"sales_order": {"fields": {}}}},
        conversation_state=conversation_state,
    )


async def _prompt_sent(
    configuration: AIGatewayConfiguration,
    *,
    conversation_state: dict[str, Any],
    allowed_task_keys: frozenset[str] = frozenset(),
) -> str:
    provider = _CapturingProvider("models/test-standard", _ACTION)
    pool = AIRoutePool((_route(provider, allowed_task_keys=allowed_task_keys),), configuration)
    gateway = RoutePoolReasoningModelGateway(
        settings=_settings(), configuration=configuration, route_pool=pool
    )
    await gateway.decide(_context(conversation_state))
    return provider.requests[-1].system_prompt


@pytest.mark.asyncio
async def test_a_turn_is_sent_the_prompt_for_the_stage_it_is_in(
    configuration: AIGatewayConfiguration,
) -> None:
    """End to end, through the real gateway, to the bytes a provider receives."""
    for conversation_state, stage in (
        ({}, ReasoningStage.OPENING),
        ({"orderSearchCache": {"totalFound": 4, "shown": 4}}, ReasoningStage.NARROWING),
        ({"orderSearchCache": {"totalFound": 1, "shown": 1}}, ReasoningStage.COMPLETING),
        ({"orderSearchCache": {"totalFound": 0, "shown": 0}}, ReasoningStage.UNRESOLVED),
    ):
        sent = await _prompt_sent(configuration, conversation_state=conversation_state)
        expected = configuration.tasks[STAGE_TASK_IDS[stage]].systemPrompt
        assert sent.startswith(expected), f"{stage.value} was not sent its own prompt"


@pytest.mark.asyncio
async def test_a_release_without_the_stage_tasks_falls_back_instead_of_failing(
    configuration: AIGatewayConfiguration,
) -> None:
    """The requirement that lets this ship at all.

    `runtime-configuration-init` in compose.yaml still runs
    `bootstrap_graph_configuration.py --if-missing`, which returns before
    comparing -- so a container deployment can be on a release published before
    these task ids existed. `StructuredOutputInvoker.__init__` raises
    `RuntimeError` for a task that is not configured, so a gateway that simply
    asked for a stage task would take the process down at startup.

    Modelled by publishing a configuration with every stage task removed, which
    is exactly what such a release looks like from the runtime's side.
    """
    without_stages = configuration.model_copy(
        update={
            "tasks": {
                task_id: task
                for task_id, task in configuration.tasks.items()
                if task_id not in set(STAGE_TASK_IDS.values())
            }
        }
    )
    assert BASE_REASONING_TASK_ID in without_stages.tasks

    sent = await _prompt_sent(without_stages, conversation_state={})
    complete = without_stages.tasks[BASE_REASONING_TASK_ID].systemPrompt
    assert sent.startswith(complete), (
        "a release without the stage tasks did not fall back to the complete prompt"
    )


@pytest.mark.asyncio
async def test_a_stage_task_no_route_may_serve_falls_back_too(
    configuration: AIGatewayConfiguration,
) -> None:
    """The second way a stage task can be unservable, and the quieter one.

    `AIRoute.allowed_task_keys` is built from the operator's live-validation
    receipts (`runtime_integrations.ai_providers[].validated_routes[].task_key`).
    A deployment that validated its routes against `ORDER_AGENT_REASONING_V1`
    alone binds them to that id, so the stage tasks would find no candidate
    routes and every turn would fail -- not at startup, where it would be
    noticed, but on the first associate's first message.

    The packaged configuration ships no validated routes, so every route is
    unrestricted and this never fires there. That is precisely why it needs a
    test: the failure is invisible until an operator does the right thing.
    """
    sent = await _prompt_sent(
        configuration,
        conversation_state={},
        allowed_task_keys=frozenset({BASE_REASONING_TASK_ID}),
    )
    complete = configuration.tasks[BASE_REASONING_TASK_ID].systemPrompt
    assert sent.startswith(complete), (
        "a stage task no route is bound to did not fall back to the complete prompt"
    )


@pytest.mark.asyncio
async def test_a_correction_is_held_to_the_same_rules_that_produced_the_action(
    configuration: AIGatewayConfiguration,
) -> None:
    """Corrections take the stage prompt, not a prompt of their own.

    A correction repairs an action produced under a particular set of rules.
    Handing it a different set would be asking it to fix an answer against a
    standard the answer was never held to -- and the correction paths are where
    a turn is already going wrong, which is the worst place to change the rules.
    """
    provider = _CapturingProvider("models/test-standard", _ACTION)
    pool = AIRoutePool((_route(provider),), configuration)
    gateway = RoutePoolReasoningModelGateway(
        settings=_settings(), configuration=configuration, route_pool=pool
    )
    state = {"orderSearchCache": {"totalFound": 4, "shown": 4}}
    await gateway.decide(_context(state))
    await gateway.correct_action(
        context=_context(state), invalid_action=None, validation_error="bad statement_type"
    )

    narrowing = configuration.tasks[STAGE_TASK_IDS[ReasoningStage.NARROWING]].systemPrompt
    assert len(provider.requests) == 2
    for request in provider.requests:
        assert request.system_prompt.startswith(narrowing)

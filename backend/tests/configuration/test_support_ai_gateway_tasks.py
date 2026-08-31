"""V2: the two support-analysis tasks, as the release actually carries them.

Contracts.md sect. 9 and sect. 10. Three things are worth asserting about a
released prompt, and none of them is its wording:

* the **shared tone/disclosure anchors** are on both tasks -- sect. 9 requires
  every `support.*` task to carry them, and an anchor is what makes that a
  property of the file rather than a convention;
* `allowedInputKeys` matches the payload the dispatcher actually sends, key for
  key -- a mismatch is a task that either drops what it needs or accepts what it
  should not see;
* the tasks are usable by `StructuredOutputInvoker` at all: STANDARD tier, and
  the simulator not among their providers, because a reasoning loop parsing
  simulator output as a real structured answer would be acting on fiction.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from return_platform.ai.routing.tasks import AIGatewayConfiguration, ModelTier

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "ai_gateway.yaml"

CLASSIFY = "support.message.classify.v1"
EXTRACT = "support.message.extract.v1"

#: The payload keys `SupportMessageAnalyser` sends for each stage. Spelled here
#: rather than imported so the test states the contract from the outside; the
#: assertion below is what pins them together.
CLASSIFY_PAYLOAD_KEYS = {"bodyText", "intents"}
EXTRACT_PAYLOAD_KEYS = {"bodyText", "intent"}


@pytest.fixture(scope="module")
def configuration() -> AIGatewayConfiguration:
    return AIGatewayConfiguration.model_validate(
        yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    )


@pytest.mark.parametrize("task_id", [CLASSIFY, EXTRACT])
def test_the_release_carries_the_task(configuration: AIGatewayConfiguration, task_id: str) -> None:
    assert task_id in configuration.tasks


@pytest.mark.parametrize("task_id", [CLASSIFY, EXTRACT])
def test_both_tasks_carry_the_shared_tone_and_disclosure_anchors(
    configuration: AIGatewayConfiguration, task_id: str
) -> None:
    """Sect. 9's requirement, asserted as identity rather than as presence.

    The two sections must be *the same text* on both tasks, not merely present
    on both -- that is what "shared anchors" means, and a copy that drifted
    would leave one task disclosing differently from the other with nothing
    saying so.
    """
    task = configuration.tasks[task_id]
    names = [section.name for section in task.systemPromptSections]
    assert names[:2] == ["support-untrusted-input", "support-tone-and-disclosure"], (
        "the untrusted-input framing and the disclosure must come first: prompt "
        "order is meaning, and a task that states its job before its constraints "
        "has stated the constraints as an afterthought"
    )


def test_the_anchors_are_one_text_shared_by_both_tasks(
    configuration: AIGatewayConfiguration,
) -> None:
    classify = {s.name: s.text for s in configuration.tasks[CLASSIFY].systemPromptSections}
    extract = {s.name: s.text for s in configuration.tasks[EXTRACT].systemPromptSections}
    for anchor in ("support-untrusted-input", "support-tone-and-disclosure"):
        assert classify[anchor] == extract[anchor], f"{anchor} has drifted between tasks"


def test_the_untrusted_input_anchor_forbids_what_the_tool_boundary_forbids(
    configuration: AIGatewayConfiguration,
) -> None:
    """Sect. 9's tool boundary, stated in the prompt as well as enforced in code.

    The enforcement is code -- raw support text can never name a tool because
    nothing passes it to one. The prompt says so too, because a model that has
    been told the rule is a model that will not spend output tokens trying.
    """
    anchor = next(
        section.text
        for section in configuration.tasks[CLASSIFY].systemPromptSections
        if section.name == "support-untrusted-input"
    ).lower()
    assert "untrusted" in anchor
    assert "never instructions" in anchor
    assert "tool" in anchor
    assert "invent" in anchor and "identifier" in anchor


def test_the_disclosure_anchor_refuses_to_let_the_agent_present_as_a_person(
    configuration: AIGatewayConfiguration,
) -> None:
    anchor = next(
        section.text
        for section in configuration.tasks[EXTRACT].systemPromptSections
        if section.name == "support-tone-and-disclosure"
    ).lower()
    assert "automated agent" in anchor
    assert "disclosure" in anchor
    assert "never present as a person" in anchor


@pytest.mark.parametrize(
    ("task_id", "expected"),
    [(CLASSIFY, CLASSIFY_PAYLOAD_KEYS), (EXTRACT, EXTRACT_PAYLOAD_KEYS)],
)
def test_allowed_input_keys_match_the_payload_the_dispatcher_sends(
    configuration: AIGatewayConfiguration, task_id: str, expected: set[str]
) -> None:
    """Exactly, in both directions.

    A superset would let a future caller hand the model a key nobody reviewed;
    a subset would silently drop the one the prompt is written around.
    """
    assert set(configuration.tasks[task_id].allowedInputKeys) == expected


@pytest.mark.parametrize("task_id", [CLASSIFY, EXTRACT])
def test_the_tasks_are_usable_by_the_structured_invoker(
    configuration: AIGatewayConfiguration, task_id: str
) -> None:
    task = configuration.tasks[task_id]
    assert task.tier is ModelTier.STANDARD
    assert "SIMULATOR" not in task.allowedProviders
    assert task.fallbackStrategy.value == "MANUAL_REVIEW"


def test_the_extraction_prompt_forbids_promoting_a_loose_artifact(
    configuration: AIGatewayConfiguration,
) -> None:
    """DR-11's create-never rule, said to the model as well as enforced after it.

    The code cannot be fooled -- a group with no reference is dropped in
    `record_bindings_from_extraction` -- but a model that invents a reference
    produces an extraction whose artifacts silently disappear, which is a worse
    failure to debug than one that never happens.
    """
    prompt = configuration.tasks[EXTRACT].systemPrompt.lower()
    assert "loose artifact" in prompt
    assert "never promote" in prompt
    assert "guessed reference would create a return" in prompt


def test_the_classification_prompt_states_that_the_set_is_closed(
    configuration: AIGatewayConfiguration,
) -> None:
    prompt = configuration.tasks[CLASSIFY].systemPrompt.lower()
    assert "the set is closed" in prompt
    assert "`other`" in prompt or "other" in prompt


@pytest.mark.parametrize("task_id", [CLASSIFY, EXTRACT])
def test_the_prompt_version_names_the_task_and_its_version(
    configuration: AIGatewayConfiguration, task_id: str
) -> None:
    """Bumped, never amended: the version is what a case's record pinned."""
    version = configuration.tasks[task_id].promptVersion
    assert version.endswith("-v1")
    assert version.startswith("support-message-")

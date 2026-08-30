"""`routing_policy_version` has a production source (phase 1b item C).

`StructuredStageInvoker.__init__` used to take it as a required free string, and
nothing in `src/` produced one -- so the only way to construct the analyser was
to type a literal at the wiring site. contracts sect. 5 asks each stage to pin a
`routing_policy_version` **before** invoking; a pin that a wiring site invents
records nothing, and a pin that only moves when somebody remembers to bump a
string is not a version.

It is now derived from the **released** AI gateway document, so changing routing
policy is a released change. These tests run against the shipped
`config/ai_gateway.yaml` and its real `support.message.*` tasks -- against
production's own document, not a fixture built to agree with the code.

The property has two halves and both are asserted, because only one of them is
obvious:

* it **moves** when something that decides where a call can go changes;
* it **does not move** when something that rides the same release but does not
  route changes. A version that bumped on a prompt fix or a price correction
  would be a version nobody could reason from, and "digest the whole document"
  is the easy wrong answer this half rules out.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from return_platform.ai.routing.tasks import (
    AIGatewayConfiguration,
    ModelTier,
    TaskConfiguration,
    load_ai_gateway_configuration,
)
from return_platform.operations.return_support.analysis_wiring import (
    derive_routing_policy_version,
)
from return_platform.operations.return_support.composition import (
    CLASSIFY_TASK_ID,
    EXTRACT_TASK_ID,
)

GATEWAY_PATH = Path(__file__).resolve().parents[2] / "config" / "ai_gateway.yaml"


@pytest.fixture(scope="module")
def released() -> AIGatewayConfiguration:
    return load_ai_gateway_configuration(GATEWAY_PATH).configuration


def _task(released: AIGatewayConfiguration, task_id: str) -> TaskConfiguration:
    task = released.tasks.get(task_id)
    assert task is not None, f"{task_id} is not in the released document"
    return task


def _version(released: AIGatewayConfiguration, task_id: str) -> str:
    return derive_routing_policy_version(released, _task(released, task_id))


def _with_task(
    released: AIGatewayConfiguration, task_id: str, **changes: object
) -> AIGatewayConfiguration:
    """The released document with one task field changed, revalidated.

    Built through `model_copy` on the real objects rather than by editing a
    dict, so a change this test makes is one a release could actually make.
    """
    task = _task(released, task_id).model_copy(update=changes)
    return released.model_copy(update={"tasks": {**released.tasks, task_id: task}})


def test_the_version_is_stable_for_an_unchanged_release(
    released: AIGatewayConfiguration,
) -> None:
    """Same document, same version -- twice, and across a `model_copy` round trip.

    Stability is the half that makes the pin usable: a version that differed
    between two reads of one release would make every analysis record's pin
    unique and say nothing.
    """
    first = _version(released, CLASSIFY_TASK_ID)
    assert first == _version(released, CLASSIFY_TASK_ID)
    assert first == _version(released.model_copy(deep=True), CLASSIFY_TASK_ID)
    assert first.startswith(f"{released.schemaVersion}:")


def test_the_two_support_stages_do_not_share_a_routing_policy_version(
    released: AIGatewayConfiguration,
) -> None:
    """Per task, because routing is per task.

    One version for the whole document would say the same thing about a stage
    whose providers had changed and a stage whose had not.
    """
    assert _version(released, CLASSIFY_TASK_ID) != _version(released, EXTRACT_TASK_ID)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("allowedProviders", ("GOOGLE",)),
        ("tier", ModelTier.LIGHTWEIGHT),
        ("allowTierEscalation", True),
        ("maximumInputTokens", 9_000),
        ("fallbackTemplate", "some-other-template"),
    ],
)
def test_a_release_that_changes_where_a_call_can_go_moves_the_version(
    released: AIGatewayConfiguration, field: str, value: object
) -> None:
    """Each routing field, one at a time.

    Parameterised rather than changed all at once: a projection that had
    accidentally dropped one field would still pass a combined check, because
    the others would move the digest for it.
    """
    reference = _version(released, CLASSIFY_TASK_ID)
    changed = _with_task(released, CLASSIFY_TASK_ID, **{field: value})
    assert derive_routing_policy_version(changed, _task(changed, CLASSIFY_TASK_ID)) != reference


def test_a_release_that_changes_the_prompt_does_not_move_the_version(
    released: AIGatewayConfiguration,
) -> None:
    """The half that rules out "just digest the document".

    `promptVersion` is pinned separately on the same analysis record as
    `release_id`. Folding it in here would make the two fields say one thing
    twice, and would report a routing-policy change every time a prompt was
    reworded.
    """
    reference = _version(released, CLASSIFY_TASK_ID)
    changed = _with_task(released, CLASSIFY_TASK_ID, promptVersion="support-classify-v99")
    assert derive_routing_policy_version(changed, _task(changed, CLASSIFY_TASK_ID)) == reference


def test_a_release_that_changes_another_task_does_not_move_this_ones_version(
    released: AIGatewayConfiguration,
) -> None:
    """Routing policy is a property of a task, not of the file it arrived in."""
    reference = _version(released, CLASSIFY_TASK_ID)
    changed = _with_task(released, EXTRACT_TASK_ID, allowedProviders=("GOOGLE",))
    assert derive_routing_policy_version(changed, _task(changed, CLASSIFY_TASK_ID)) == reference
    # And the task that *did* change moved, so this is not simply inert.
    assert derive_routing_policy_version(changed, _task(changed, EXTRACT_TASK_ID)) != _version(
        released, EXTRACT_TASK_ID
    )


def test_a_document_level_routing_change_moves_every_tasks_version(
    released: AIGatewayConfiguration,
) -> None:
    """Circuit-breaker and provider limits decide where a call can go too.

    They are not task fields, so a projection built only from the task would
    miss them entirely and report an unchanged policy across a change that
    reroutes every call in the process.
    """
    reference = _version(released, CLASSIFY_TASK_ID)
    breaker = released.circuitBreaker.model_copy(
        update={"failureThreshold": released.circuitBreaker.failureThreshold + 1}
    )
    changed = released.model_copy(update={"circuitBreaker": breaker})
    assert derive_routing_policy_version(changed, _task(changed, CLASSIFY_TASK_ID)) != reference


def test_nothing_in_src_supplies_a_routing_policy_version_as_a_literal() -> None:
    """The grep the reviewer runs, run here instead.

    Item C's actual failure mode was not a wrong value -- it was that the only
    way to produce one was to type it. If a literal ever comes back this fails,
    including at a wiring site nobody thought of as part of this slice.

    Matches a **string literal** on the right-hand side, not the keyword: the
    analyser's own `routing_policy_version=invoker.routing_policy_version` is
    the pin sect. 5 asks for and is exactly what this is protecting.
    """
    root = Path(__file__).resolve().parents[2] / "src"
    literal = re.compile(r"""routing_policy_version\s*=\s*(f?["'])""")
    offenders = [
        f"{path.relative_to(root)}:{number}"
        for path in root.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if literal.search(line)
    ]
    assert offenders == [], (
        "routing_policy_version must be derived from the released configuration, "
        f"never passed in: {offenders}"
    )

"""Architecture test: AgentExecutionContext stays platform-neutral.

Design intent (agents/contracts/context.py docstring): no `.ai`, no `.knowledge`, no
other agent, no domain type. A context with a named module field would force a second
refactor once LangGraph and Temporal orchestration land on top of it -- this test
pins the exact field set so a new field can't be added by accident without a
deliberate edit here.
"""

from __future__ import annotations

import dataclasses

from return_platform.agents.contracts.context import AgentExecutionContext

EXPECTED_FIELD_NAMES = frozenset(
    {
        "configuration",
        "capabilities",
        "audit",
        "redactor",
        "principal",
        "correlation_id",
        "session_id",
        "configuration_release_id",
        "clock",
        "consistency",
    }
)

FORBIDDEN_FIELD_NAME_SUBSTRINGS = ("ai", "knowledge", "graph", "agent", "gateway")


def test_field_set_matches_exactly() -> None:
    actual = frozenset(field.name for field in dataclasses.fields(AgentExecutionContext))
    assert actual == EXPECTED_FIELD_NAMES, (
        f"AgentExecutionContext's field set changed: {actual}. If this is a "
        "deliberate, reviewed addition, update EXPECTED_FIELD_NAMES here too."
    )


def test_no_field_name_suggests_a_module_specific_dependency() -> None:
    violations = [
        field.name
        for field in dataclasses.fields(AgentExecutionContext)
        for needle in FORBIDDEN_FIELD_NAME_SUBSTRINGS
        if needle in field.name.lower()
    ]
    assert not violations, (
        f"AgentExecutionContext gained a field naming a module-specific dependency: "
        f"{violations}. Agents resolve module capabilities from `capabilities` "
        "instead of a named field (design R2a)."
    )

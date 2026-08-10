"""One release lifecycle table, shared by everything that enforces it.

Wave D3, following the decision to make the graph lifecycle authoritative (see
`docs/CONFIGURATION_RELEASE_LIFECYCLE_DECISION.md`).

The table was written out three times: `InMemoryConfigurationGraphRepository`,
`Neo4jConfigurationGraphRepository`, and the Data Console router, which
pre-checks so a refused promotion answers 409 before the domain validation does
any work. Three copies of a state machine is three chances for a transition to
be legal in one place and refused in another.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from return_platform.configuration.graph_repository import (
    RELEASE_TRANSITIONS,
    InMemoryConfigurationGraphRepository,
    transition_allowed,
)

_SRC = Path(__file__).resolve().parents[2] / "src" / "return_platform"


def test_no_module_writes_its_own_transition_table() -> None:
    """The literal shape the three copies had, banned by structure.

    Matches any dict whose keys include the release states and whose values are
    sets of strings -- the form a reimplementation would naturally take. The
    definition of `RELEASE_TRANSITIONS` itself uses `frozenset(...)` calls, so it
    does not match its own ban.
    """
    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = {
                key.value
                for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            if not {"DRAFT", "VALIDATED"} <= keys:
                continue
            if any(isinstance(value, ast.Set) for value in node.values):
                offenders.append(f"{path.relative_to(_SRC)}:{node.lineno}")
    assert offenders == [], f"a second release-transition table appeared: {offenders}"


@pytest.mark.parametrize(
    ("current", "target", "expected"),
    [
        ("DRAFT", "VALIDATED", True),
        ("DRAFT", "ARCHIVED", True),
        ("DRAFT", "RELEASED", False),
        ("VALIDATED", "RELEASED", True),
        ("VALIDATED", "ARCHIVED", True),
        ("VALIDATED", "DRAFT", False),
        ("SUPERSEDED", "ARCHIVED", True),
        ("SUPERSEDED", "RELEASED", False),
        ("ARCHIVED", "DRAFT", False),
        ("RELEASED", "ARCHIVED", False),
        ("RELEASED", "SUPERSEDED", False),
    ],
)
def test_the_table_says_what_it_always_said(current: str, target: str, expected: bool) -> None:
    """Pinned so the extraction is provably behaviour-preserving. Every case
    here is what the three copies did before they were collapsed."""
    assert transition_allowed(current, target) is expected


def test_an_unknown_state_permits_nothing() -> None:
    """A release whose status is not in the table -- corrupt data, or a state
    added to the graph without being added here -- must not be promotable. The
    `.get(..., frozenset())` default is doing real work."""
    assert not transition_allowed("NOT_A_STATE", "RELEASED")
    assert not transition_allowed("", "ARCHIVED")


def test_released_is_terminal_by_promotion() -> None:
    """`RELEASED` has no outgoing transition on purpose: a release leaves it only
    by being superseded when its successor publishes, which the publish
    transaction does atomically. Exposing `RELEASED -> SUPERSEDED` as a
    promotion would let an operator retire the live configuration without a
    replacement -- and production refuses to start with no active release."""
    assert "RELEASED" not in RELEASE_TRANSITIONS


@pytest.mark.asyncio
async def test_the_repository_enforces_the_shared_table() -> None:
    """Not just that the constant exists -- that the code path uses it."""
    repository = InMemoryConfigurationGraphRepository()
    await repository.save_draft_domain("r-1", "returns", {"a": 1}, "tester")

    with pytest.raises(ValueError, match="Invalid configuration transition DRAFT -> RELEASED"):
        await repository.promote_release("r-1", "RELEASED", "tester", expected_head_revision=0)

    promoted = await repository.promote_release("r-1", "VALIDATED", "tester")
    assert promoted.status == "VALIDATED"

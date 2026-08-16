"""The console's mocks and fixtures may only speak capabilities the agent allows.

The defect this closes is the capability-level twin of the one
`tests/configuration/test_copilot_agent_binding.py` closes. That one compares
the *agent id*; this one compares the *business capability*. They are different
identifiers and they drifted separately: the Copilot once sent the agent id
`"order_discovery"` where the schema keys `order-discovery-agent`, and the
frontend's turn fixtures separately carried the capability `"order_discovery"`
where the schema allows `order-discovery`. Both spellings look right and both
are refused -- the second by `CapabilityGuard`, with
`ORDER_AGENT_INVALID_CAPABILITY`, on every turn.

Nothing was watching the second one, and structurally nothing could be:

* OpenAPI types `StructuredAgentResponse.business_capability` as a bare `str`,
  so the frontend's contract test -- which validates every mock body against the
  committed document -- passes any string at all. It says so itself, and points
  at this file.
* The vocabulary is not in OpenAPI. It lives in
  `agent_policies['order-discovery-agent'].allowed_business_capabilities` in the
  active schema, which is a deployment artefact rather than an API contract.

So the comparison has to be made somewhere that can read both, which is here.

**The allowed set is read from the YAML at test time and is never written down
in this file.** A hardcoded copy would be a third place for the vocabulary to
live and would recreate exactly the drift this exists to prevent: it would keep
passing while the schema moved underneath it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

from return_platform.configuration.settings import DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH
from return_platform.dynamic_knowledge.config_loader import load_active_schema

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
FRONTEND_SOURCE_ROOT: Final = REPOSITORY_ROOT / "frontend" / "src"

#: The agent the console talks to. The same id
#: `tests/configuration/test_copilot_agent_binding.py` proves is bound.
COPILOT_AGENT_ID: Final = "order-discovery-agent"

#: Generated from the backend and overwritten wholesale by `contracts:check`;
#: it declares the *type* of the field, never a value, so it has no literals to
#: check and any it appeared to have would not be hand-written.
EXCLUDED_SUBTREES: Final = (FRONTEND_SOURCE_ROOT / "api" / "generated",)

#: Below this, assume the scan broke rather than that the console got smaller.
#: A source-parsing guard that silently matches nothing is worse than no guard:
#: it is a green check that stopped checking. Ten literals across three files
#: exist today; the floor is set under that so ordinary edits do not trip it
#: while a wholesale rename of the field still does.
MINIMUM_EXPECTED_LITERALS: Final = 6
MINIMUM_EXPECTED_FILES: Final = 2

_FIELD = re.compile(r"business_capability\s*:")
_STRING_LITERAL = re.compile(r"""(?:"([^"\\\n]*)"|'([^'\\\n]*)')""")

#: How far past the field name to look for the value. Long enough for a
#: multi-line or ternary assignment, short enough that a runaway match cannot
#: swallow an unrelated part of the file.
_MAX_VALUE_SPAN: Final = 600


def _value_expression(source: str, start: int) -> str:
    """The text assigned to `business_capability`, from `start` to its end.

    Consumes to the first comma or semicolon at bracket depth zero, which
    terminates an object property in every form the console writes one:

        business_capability: "order-discovery",
        business_capability: isDirect ? "order-discovery" : "candidate-disambiguation",
        business_capability:
          "order-discovery",

    Commas nested inside a call, array or object belong to the value and are
    stepped over. A closing bracket at negative depth ends the enclosing object
    and therefore the value too -- that is the trailing-comma-free last property.
    """
    depth = 0
    end = min(start + _MAX_VALUE_SPAN, len(source))
    for index in range(start, end):
        character = source[index]
        if character in "([{":
            depth += 1
        elif character in ")]}":
            if depth == 0:
                return source[start:index]
            depth -= 1
        elif character in ",;" and depth == 0:
            return source[start:index]
    return source[start:end]


def _capability_literals(source: str) -> list[str]:
    """Every string literal assigned to `business_capability` in one file.

    A type declaration (`business_capability: string;`) and a prose mention in a
    comment contribute nothing, having no string literal in the assigned value.
    A ternary contributes both of its branches, because the mock can answer with
    either.
    """
    found: list[str] = []
    for match in _FIELD.finditer(source):
        expression = _value_expression(source, match.end())
        for literal in _STRING_LITERAL.finditer(expression):
            found.append(literal.group(1) if literal.group(1) is not None else literal.group(2))
    return found


@pytest.fixture(scope="module")
def allowed_capabilities() -> frozenset[str]:
    """The vocabulary, from the file that is its authority.

    `active-schema.return-order.yaml` rather than `active-schema.example.yaml`:
    the return-order schema is what `Settings` defaults to, what `.env.example`
    points `PLATFORM_DYNAMIC_KNOWLEDGE_SCHEMA_PATH` at, and therefore what
    `CapabilityGuard` is holding when it refuses a turn in a real process. The
    example schema is a loader fixture with a deliberately smaller policy.
    """
    schema = load_active_schema(DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH)
    policy = schema.agent_policies.get(COPILOT_AGENT_ID)
    assert policy is not None, (
        f"{DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH} declares no policy for "
        f"{COPILOT_AGENT_ID!r}; the console has no vocabulary to conform to. "
        f"Policies present: {sorted(schema.agent_policies)}"
    )
    return frozenset(policy.allowed_business_capabilities)


@pytest.fixture(scope="module")
def console_capability_literals() -> dict[Path, list[str]]:
    """Every hand-written capability literal in the console, by file.

    Read out of the source rather than imported from a shared constant on
    purpose. A shared constant only covers the occurrences that opted into it,
    so the eleventh one someone types inline -- which is how all ten of the
    current ones were written -- is invisible to it. Parsing is exhaustive by
    construction: a new occurrence is checked because it exists, not because its
    author remembered this test.
    """
    if not FRONTEND_SOURCE_ROOT.is_dir():
        pytest.skip(f"No frontend checkout at {FRONTEND_SOURCE_ROOT}; nothing to check.")

    literals: dict[Path, list[str]] = {}
    for path in sorted(FRONTEND_SOURCE_ROOT.rglob("*.ts*")):
        if any(excluded in path.parents for excluded in EXCLUDED_SUBTREES):
            continue
        found = _capability_literals(path.read_text(encoding="utf-8"))
        if found:
            literals[path] = found
    return literals


def test_the_scan_still_finds_the_consoles_capability_literals(
    console_capability_literals: dict[Path, list[str]],
) -> None:
    """The guard has something to guard.

    Asserted separately and first, because every other assertion here is
    vacuously true over an empty scan. If the field is renamed or the fixtures
    are restructured past what `_value_expression` understands, this fails and
    says so, rather than the suite reporting a passing check of nothing.
    """
    total = sum(len(found) for found in console_capability_literals.values())
    assert len(console_capability_literals) >= MINIMUM_EXPECTED_FILES and (
        total >= MINIMUM_EXPECTED_LITERALS
    ), (
        f"Found only {total} `business_capability` literal(s) in "
        f"{len(console_capability_literals)} file(s) under {FRONTEND_SOURCE_ROOT}, "
        f"below the floor of {MINIMUM_EXPECTED_LITERALS} in "
        f"{MINIMUM_EXPECTED_FILES}. Either the console genuinely shrank -- lower "
        f"the floor deliberately -- or this test's source scan has stopped "
        f"matching and is now checking nothing. Found: "
        f"{ {str(path.relative_to(REPOSITORY_ROOT)): found for path, found in console_capability_literals.items()} }"
    )


def test_every_console_capability_is_one_the_agent_policy_allows(
    console_capability_literals: dict[Path, list[str]],
    allowed_capabilities: frozenset[str],
) -> None:
    """The live check: every literal the console can send, the guard accepts.

    A failure here is a turn that would come back
    `422 ORDER_AGENT_INVALID_CAPABILITY` in front of an associate, caught
    against the YAML instead.
    """
    offenders = [
        (path, literal)
        for path, found in console_capability_literals.items()
        for literal in found
        if literal not in allowed_capabilities
    ]

    assert not offenders, (
        "The console speaks capabilities the agent policy refuses:\n"
        + "\n".join(
            f"  {path.relative_to(REPOSITORY_ROOT).as_posix()}: {literal!r}"
            for path, literal in offenders
        )
        + (
            f"\n\nAllowed for {COPILOT_AGENT_ID} by "
            f"{DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH.relative_to(REPOSITORY_ROOT).as_posix()}:\n"
            + "\n".join(f"  {capability!r}" for capability in sorted(allowed_capabilities))
            + "\n\nThe YAML is the authority. Correct the literal, not the schema."
        )
    )


def test_the_membership_check_has_teeth(allowed_capabilities: frozenset[str]) -> None:
    """A membership assertion that could never fail is indistinguishable from one
    that always passes, so the two spellings that actually shipped are named here
    and proven refused.

    `order_discovery` is the underscored capability the turn fixtures carried.
    `POLICY_EVALUATION` and `RMA_ISSUANCE` are `CopilotStage` members -- a
    different vocabulary that a previous fixture put in this field, which is its
    own kind of drift: two enums sharing a spelling for the same idea, only one
    of which the guard knows about.
    """
    for never_allowed in ("order_discovery", "POLICY_EVALUATION", "RMA_ISSUANCE"):
        assert never_allowed not in allowed_capabilities


def test_the_scan_reads_the_shapes_the_console_actually_writes() -> None:
    """`_value_expression` against each form, including ones not in the tree today.

    The extractor is the part of this test that can rot silently, so its
    behaviour is pinned rather than inferred from the files it happens to meet.
    """
    assert _capability_literals('business_capability: "order-discovery",') == ["order-discovery"]
    assert _capability_literals(
        'business_capability: isDirect ? "order-discovery" : "candidate-disambiguation",'
    ) == ["order-discovery", "candidate-disambiguation"]
    assert _capability_literals('{ status: "OK", business_capability: "order-discovery" }') == [
        "order-discovery"
    ]
    assert _capability_literals('business_capability:\n  "purchase-history",') == [
        "purchase-history"
    ]
    # A type declaration and a prose mention are not values, and must not be
    # read as ones -- a false positive here would be unfixable without
    # weakening the guard.
    assert _capability_literals("business_capability: string;") == []
    assert _capability_literals("// the agent's `business_capability` is such a field") == []

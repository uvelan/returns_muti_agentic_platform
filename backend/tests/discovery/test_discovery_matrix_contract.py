"""The matrix itself: complete, derived, and honest about what it cannot run.

The audit exercised Order Discovery zero times -- every model attempt timed out
-- so the primary user journey went unverified and the NO-GO rested partly on a
surface nobody had seen work. The matrix exists so "Discovery works" is a
countable claim rather than an impression.

These tests run in the normal suite. They assert the shape of the matrix and the
two cases that are genuinely answerable from configuration. The executions that
need the graph, the API and a model route live in the `_real_infra` module
beside this one, and skip with a reason rather than being absent -- an absent
test reads exactly like a passing one in a report.
"""

from __future__ import annotations

import pytest

from return_platform.dynamic_knowledge.order_agent.discovery_matrix import (
    MATRIX,
    Evidence,
    cases_for,
)


def test_the_matrix_declares_fifteen_cases() -> None:
    """The number is in the plan, so a case quietly dropped is a gate failure."""
    assert len(MATRIX) == 15


def test_every_case_is_uniquely_identified() -> None:
    identifiers = [case.id for case in MATRIX]
    assert len(set(identifiers)) == len(identifiers)


@pytest.mark.parametrize("case", MATRIX, ids=lambda case: case.id)
def test_every_case_says_what_it_proves(case) -> None:  # noqa: ANN001 - parametrized dataclass
    """A case whose value is not stated gets deleted by the next person to read it.

    Several of these are indistinguishable from success if you only look at the
    response -- repeated confirmation, concurrent confirmation, retry after a
    timeout that already committed -- so the reason each one exists has to travel
    with it.
    """
    assert case.description
    assert case.proves
    assert len(case.proves) > 40, f"{case.id} does not explain itself"


def test_the_matrix_is_split_by_what_each_case_needs() -> None:
    """So a run can report "12 of 15, 3 need a model route" instead of "passed".

    Two cases need a live model route, which this deployment does not have: the
    audit found both providers credentialed with none constructed. Marking them
    rather than deleting them keeps the gap countable.
    """
    assert cases_for(Evidence.CONFIGURATION), "nothing is answerable without a stack"
    assert cases_for(Evidence.STACK)
    assert cases_for(Evidence.MODEL_ROUTE), (
        "the two cases that need a model route must stay declared; deleting them "
        "would make the matrix look complete on a deployment that cannot run them"
    )
    assert sum(len(cases_for(evidence)) for evidence in Evidence) == len(MATRIX)


def test_the_case_that_counts_a_case_asserts_the_workflow_too() -> None:
    """Closure is one case *and* one workflow start, never one HTTP 201.

    Asserted on the declaration, because this is the property the whole matrix
    is built around and it is the one most likely to be weakened by someone
    trying to get a red suite green.
    """
    confirmation = next(
        case for case in MATRIX if case.id == "confirmation-creates-exactly-one-case"
    )
    assert "workflow" in confirmation.description.lower()
    assert "201" in confirmation.proves or "datastore" in confirmation.proves


def test_the_identification_field_case_is_derived_rather_than_listed() -> None:
    """Seventeen field names were once hardcoded in Order Discovery.

    The matrix must not reintroduce that: a case that names its fields is stale
    the moment an operator adds one, and the tenth field would go untested while
    the suite stayed green.
    """
    field_case = next(case for case in MATRIX if case.id == "every-configured-identification-field")
    assert field_case.evidence is Evidence.CONFIGURATION
    assert "read from" in field_case.proves or "at test time" in field_case.proves


def test_an_unresolvable_configured_field_has_somewhere_to_be_reported() -> None:
    """Case 14 is answerable today, because the catalogue already models it.

    `IdentificationCatalogue.unresolved` collects fields naming an entity or
    property the active schema does not have, at construction rather than per
    turn. The matrix case exists to keep that surfaced.
    """
    from return_platform.dynamic_knowledge.order_agent.identification import (
        IdentificationCatalogue,
    )

    catalogue = IdentificationCatalogue()
    assert catalogue.unresolved == ()
    assert hasattr(catalogue, "intent_keys")

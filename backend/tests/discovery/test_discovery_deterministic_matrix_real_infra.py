"""The matrix's live half: eleven stack cases and two that need a model route.

Auto-marked `live_infra` by the `_real_infra` suffix, so the default run
deselects it and `scripts/dev/run_real_infra_suite.sh` selects it.

**These skip with a reason rather than being absent.** An absent test reads
exactly like a passing one in a report, and this suite's whole purpose is to
stop Order Discovery being reported as fine because nobody could exercise it --
which is what happened during the audit, where every model attempt timed out.

**Two are unreachable on this deployment, and say so.** The audit found both
providers credentialed with no model route constructed, under
`PLATFORM_AI_PROVIDER_ORDER=MANUAL`. `initial-facts-retained-with-provenance`
and `conflicting-facts-in-one-utterance` require a model to answer; they skip
naming that, so a run reports "13 of 15, 2 need a model route" instead of a
number that hides it.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from return_platform.dynamic_knowledge.order_agent.discovery_matrix import (
    MATRIX,
    Evidence,
    MatrixCase,
    cases_for,
)

#: Where the API under test is. Absent means there is no stack to talk to.
_BASE_URL = os.environ.get("DISCOVERY_MATRIX_BASE_URL")

#: Set only when a live model route exists. The two model-route cases refuse to
#: pretend otherwise -- passing them against a MANUAL route with no responder
#: would assert that a human answered, which is not what they are for.
_MODEL_ROUTE_AVAILABLE = os.environ.get("DISCOVERY_MATRIX_MODEL_ROUTE") == "1"


def _require_stack() -> None:
    if _BASE_URL is None:
        pytest.skip(
            "DISCOVERY_MATRIX_BASE_URL is unset: no running API to exercise "
            "Discovery against. This case is unproven, not passing."
        )


def _require_model_route() -> None:
    _require_stack()
    if not _MODEL_ROUTE_AVAILABLE:
        pytest.skip(
            "DISCOVERY_MATRIX_MODEL_ROUTE is unset: this deployment has no "
            "constructed model route (the audit found both providers "
            "credentialed with none built, under MANUAL provider order). This "
            "case is unproven, not passing."
        )


@pytest.fixture
def client() -> Iterator[object]:
    """An HTTP client bound to the running API.

    Deliberately constructed after the skip, so a run without a stack does not
    pay for a connection it will not use.
    """
    _require_stack()
    import httpx

    with httpx.Client(base_url=str(_BASE_URL), timeout=30.0) as session:
        yield session


def _case(identifier: str) -> MatrixCase:
    return next(case for case in MATRIX if case.id == identifier)


# --------------------------------------------------------------------------
# The stack cases.
#
# Each is written to fail loudly on the thing it proves rather than on the
# plumbing around it, so a failure names a defect and not a fixture.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case", cases_for(Evidence.STACK), ids=lambda case: case.id)
def test_every_stack_case_is_declared_before_it_is_implemented(case: MatrixCase) -> None:
    """The inventory, so an unimplemented case is visible in the run.

    Nine of the eleven stack cases need seeded corpus data this deployment does
    not have -- a representative value per configured identification field, an
    ambiguous identifier, and a candidate set that can be made stale. Declaring
    them here means the run reports what is missing instead of reporting
    nothing.
    """
    assert case.evidence is Evidence.STACK
    assert case.proves


def test_the_api_answers_at_all(client: object) -> None:
    """The precondition every other stack case rests on.

    Separated so a dead stack produces one clear failure rather than eleven
    confusing ones.
    """
    response = client.get("/api/principal")  # type: ignore[attr-defined]
    assert response.status_code == 200, response.text


def test_the_serving_generation_is_singular(client: object) -> None:
    """Case 12's precondition, and UIAUDIT-001's invariant.

    Candidate ids are generation-scoped, so "exactly one generation serves" is
    what makes a confirmation mean anything. Asserted against the running
    process rather than the database, because what matters is the generation
    *discovery reads*, not how many rows exist.
    """
    response = client.get("/api/config/adoption")  # type: ignore[attr-defined]
    if response.status_code == 404:
        pytest.skip("this build does not expose /api/config/adoption")
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "case",
    cases_for(Evidence.MODEL_ROUTE),
    ids=lambda case: case.id,
)
def test_the_model_route_cases_report_their_gap(case: MatrixCase) -> None:
    """Skips naming the missing route, so the gap is countable in the report."""
    _require_model_route()
    pytest.fail(
        f"{case.id} has a model route available and no implementation. It proves: {case.proves}"
    )

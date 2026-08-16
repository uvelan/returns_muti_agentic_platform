"""The return-method requirement table is operator-owned data, in the release.

It used to be `DEFAULT_RETURN_METHOD_REQUIREMENTS`, a Python constant in
`operations/case_projection/completion.py`. That is the wrong owner: the table
keys `return_policy.normalized_return_methods`, so an operator adding a return
method through the Control Centre had to also ship code before any case using it
could ever complete -- and until they did, the method was unmapped and every such
return waited forever without anything looking broken.

It now lives in `return_policy.return_method_requirements`, beside the catalogue
it keys, so both halves of "this deployment supports OFFSITE_HEAVY_PICKUP" are
one release.

Moving validated data is where guards get lost, so the three that make the table
safe are re-proven here against the configuration path rather than the constant:

* every row must require `RMA`;
* no row may name `UNKNOWN`;
* a method with **no** row is unmapped -- the case awaits `RETURN_METHOD` rather
  than reporting that it requires nothing. An empty requirement set has to stay
  unreachable, because reaching it is how a rejected or unresolved case reads as
  business-complete.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    build_return_method_requirement_table,
    load_return_configuration,
)
from return_platform.configuration.settings import DEFAULT_RETURN_CONFIGURATION_PATH
from return_platform.operations.case_projection import (
    AwaitingDimension,
    CaseProjectionState,
    NormalizedReturnMethod,
    PolicyEvaluationProjection,
    ReturnCaseStatus,
    ReturnRecordProjection,
    resolve_completion,
)
from return_platform.policy import EligibilityDecision, PolicyRoute

#: The table the plan states, plus the four rows inferred from the catalogue
#: when it moved out of code. Written out here rather than read from the YAML so
#: that this is an assertion about *content* -- a row edited in the release
#: without anyone deciding to fails here, which is the point of pinning it.
EXPECTED_ROWS: dict[str, set[AwaitingDimension]] = {
    # --- Stated verbatim in the remediation plan (sect. 6.4).
    "PREPAID_PARCEL": {AwaitingDimension.RMA, AwaitingDimension.LABEL, AwaitingDimension.TRACKING},
    "BRANCH_LTL": {AwaitingDimension.RMA, AwaitingDimension.BOL, AwaitingDimension.PICKUP},
    "OFFSITE_LTL": {
        AwaitingDimension.RMA,
        AwaitingDimension.BOL,
        AwaitingDimension.PICKUP,
        AwaitingDimension.RETURN_LOCATION,
    },
    "CUSTOMER_KEEP": {AwaitingDimension.RMA},
    "NO_PHYSICAL_RETURN": {AwaitingDimension.RMA},
    # --- Inferred, and flagged in the YAML as needing operator review.
    "BRANCH_UPS": {AwaitingDimension.RMA, AwaitingDimension.LABEL, AwaitingDimension.TRACKING},
    "OFFSITE_PARCEL": {
        AwaitingDimension.RMA,
        AwaitingDimension.LABEL,
        AwaitingDimension.TRACKING,
        AwaitingDimension.RETURN_LOCATION,
    },
    "DIRECT_VENDOR": {AwaitingDimension.RMA, AwaitingDimension.RETURN_LOCATION},
    "FIELD_SCRAP": {AwaitingDimension.RMA},
}

#: The four rows no source states. Marked in the release so an operator knows
#: which ones are a reading rather than a decision.
INFERRED_METHODS = frozenset({"BRANCH_UPS", "OFFSITE_PARCEL", "DIRECT_VENDOR", "FIELD_SCRAP"})


@pytest.fixture(scope="module")
def shipped_configuration() -> ReturnPlatformConfiguration:
    return load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH).configuration


@pytest.fixture(scope="module")
def shipped_payload() -> dict[str, Any]:
    parsed: Any = yaml.safe_load(DEFAULT_RETURN_CONFIGURATION_PATH.read_bytes())
    assert isinstance(parsed, dict)
    return parsed


# --- The table loads from the release ----------------------------------------


def test_the_requirement_table_comes_from_the_release(
    shipped_configuration: ReturnPlatformConfiguration,
) -> None:
    table = build_return_method_requirement_table(shipped_configuration)

    assert {str(row.method): set(row.requires) for row in table.rows} == EXPECTED_ROWS


def test_every_method_in_the_catalogue_is_mapped_except_unknown(
    shipped_configuration: ReturnPlatformConfiguration,
) -> None:
    """The catalogue and the table are released together, so they agree.

    `UNKNOWN` is the one member with no row, by construction rather than by
    omission -- see the guard below.
    """
    catalogue = set(shipped_configuration.return_policy.normalized_return_methods)

    assert catalogue - set(EXPECTED_ROWS) == {"UNKNOWN"}


def test_a_row_naming_a_method_outside_the_catalogue_is_refused(
    shipped_payload: dict[str, Any],
) -> None:
    """The table keys the catalogue; a row for a method that does not exist is a typo."""
    payload = _payload_with_rows(
        shipped_payload,
        [*_rows(shipped_payload), {"method": "TELEPORT", "requires": ["RMA"]}],
    )

    with pytest.raises(ValidationError, match="not in normalized_return_methods"):
        ReturnPlatformConfiguration.model_validate(payload)


def test_the_inferred_rows_are_flagged_for_operator_review() -> None:
    """The four rows nothing states are marked as such in the release.

    A reading recorded as though it were a decision is how an inference becomes
    permanent. The banner is what an operator reviewing the file needs, so it is
    load-bearing and asserted rather than left to survive the next edit.
    """
    text = DEFAULT_RETURN_CONFIGURATION_PATH.read_text(encoding="utf-8")
    _, _, after_table = text.partition("return_method_requirements:")
    section, _, _ = after_table.partition("bol_tendering_instruction_types:")

    assert "OPERATOR REVIEW REQUIRED" in section
    banner_position = section.index("OPERATOR REVIEW REQUIRED")
    for method in INFERRED_METHODS:
        assert section.index(f"method: {method}") > banner_position, (
            f"{method} is inferred and must sit under the review banner"
        )
    for method in set(EXPECTED_ROWS) - INFERRED_METHODS:
        assert section.index(f"method: {method}") < banner_position, (
            f"{method} is stated by the plan and must sit above the review banner"
        )


# --- Guard 1: every row requires RMA -----------------------------------------


def test_a_row_without_rma_is_refused(shipped_payload: dict[str, Any]) -> None:
    """A method that completes without an authorization completes without a return."""
    payload = _payload_with_rows(
        shipped_payload,
        [{"method": "PREPAID_PARCEL", "requires": ["LABEL", "TRACKING"]}],
    )

    with pytest.raises(ValidationError, match="must require RMA"):
        ReturnPlatformConfiguration.model_validate(payload)


def test_every_shipped_row_requires_rma(
    shipped_configuration: ReturnPlatformConfiguration,
) -> None:
    table = build_return_method_requirement_table(shipped_configuration)

    assert all(AwaitingDimension.RMA in row.requires for row in table.rows)


# --- Guard 2: no row for UNKNOWN ---------------------------------------------


def test_a_row_for_unknown_is_refused(shipped_payload: dict[str, Any]) -> None:
    """`UNKNOWN` is the absence of a method; a row for it completes an undecided case."""
    payload = _payload_with_rows(
        shipped_payload,
        [*_rows(shipped_payload), {"method": "UNKNOWN", "requires": ["RMA"]}],
    )

    with pytest.raises(ValidationError, match="UNKNOWN is the absence of a return method"):
        ReturnPlatformConfiguration.model_validate(payload)


def test_the_shipped_table_has_no_unknown_row(
    shipped_configuration: ReturnPlatformConfiguration,
) -> None:
    table = build_return_method_requirement_table(shipped_configuration)

    assert table.requirements_for("UNKNOWN") is None


# --- Guard 3: no row means unmapped, never "requires nothing" ----------------


def test_an_empty_requirement_set_cannot_be_expressed(
    shipped_payload: dict[str, Any],
) -> None:
    """The guard that keeps a rejected or unresolved case from reading as complete."""
    payload = _payload_with_rows(
        shipped_payload,
        [{"method": "PREPAID_PARCEL", "requires": []}],
    )

    with pytest.raises(ValidationError):
        ReturnPlatformConfiguration.model_validate(payload)


def test_a_method_with_no_row_leaves_the_case_awaiting_return_method(
    shipped_configuration: ReturnPlatformConfiguration,
) -> None:
    """An unmapped method waits; it does not complete because nothing was required.

    Run through `resolve_completion` with the released table rather than
    asserted on the table alone, because "requires nothing" and "not mapped"
    differ only in what the projection then does with them.
    """
    table = build_return_method_requirement_table(shipped_configuration)
    unmapped = table.model_copy(
        update={"rows": tuple(row for row in table.rows if row.method != "PREPAID_PARCEL")}
    )

    assessment = resolve_completion(
        _approved_case(NormalizedReturnMethod.PREPAID_PARCEL),
        requirements=unmapped,
    )

    assert assessment.completion_profile_resolved is False
    assert assessment.awaiting == (AwaitingDimension.RETURN_METHOD,)
    assert assessment.business_complete is False


def test_the_released_table_maps_the_same_method(
    shipped_configuration: ReturnPlatformConfiguration,
) -> None:
    """The contrast case: with the row present the profile resolves and waits on artifacts."""
    assessment = resolve_completion(
        _approved_case(NormalizedReturnMethod.PREPAID_PARCEL),
        requirements=build_return_method_requirement_table(shipped_configuration),
    )

    assert assessment.completion_profile_resolved is True
    assert set(assessment.awaiting) == {
        AwaitingDimension.RMA,
        AwaitingDimension.LABEL,
        AwaitingDimension.TRACKING,
    }


def test_unknown_is_unresolved_even_though_it_is_in_the_catalogue(
    shipped_configuration: ReturnPlatformConfiguration,
) -> None:
    assessment = resolve_completion(
        _approved_case(NormalizedReturnMethod.UNKNOWN),
        requirements=build_return_method_requirement_table(shipped_configuration),
    )

    assert assessment.awaiting == (AwaitingDimension.RETURN_METHOD,)


# --- Helpers -----------------------------------------------------------------


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: Any = payload["return_policy"]["return_method_requirements"]
    return [dict(row) for row in rows]


def _payload_with_rows(
    payload: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        **payload,
        "return_policy": {**payload["return_policy"], "return_method_requirements": rows},
    }


def _approved_case(method: NormalizedReturnMethod) -> CaseProjectionState:
    """Approved, one RMA, nothing produced yet -- so `awaiting` is the whole row."""
    now = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    return CaseProjectionState(
        caseId="CASE-1",
        tenantId="acme",
        principalId="assoc-1",
        conversationId="CONV-1",
        status=ReturnCaseStatus.PROCESSING_RETURN,
        revision=1,
        updatedAt=now,
        policyEvaluation=PolicyEvaluationProjection(
            route=PolicyRoute.STANDARD_RETURN,
            originalDecision=EligibilityDecision.APPROVE,
            effectiveDecision=EligibilityDecision.APPROVE,
            policyId="FERGUSON_RETURNS",
            policyVersion="2026-08-15",
            evaluatedAt=now,
        ),
        returnRecords=(ReturnRecordProjection(returnRecordId="REC-1", returnMethod=method.value),),
    )

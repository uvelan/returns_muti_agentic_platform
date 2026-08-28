"""§11 / audit #12: the configured restocking rate reaches the screen.

WHAT WAS BROKEN
---------------
Everything except the writer. `restocking_fee.seller_schedule` in the shipped
release holds `default_rate_basis_points: 1500`;
`policy.evaluator._seller_restocking_fee` puts it on
`FeeDetermination.rate_basis_points` with `SELLER_CONFIGURATION` beside it;
`PolicyEvaluationProjection` carries `rateBasisPoints` / `rateSource`,
validator-paired exactly as `FeeDetermination` pairs them; and
`assembly._restocking_rate` reads both out of the case fact log.

Nothing wrote the two facts. `ReturnCaseWorkflowActivities._record_policy_outcome`
recorded `policy_restocking_fee_applies` and `policy_restocking_fee_waived` off
the same `FeeDetermination` and stopped, so `rateBasisPoints` was `None` on every
case in the platform and the fee pane could report only that a fee applied.

WHY THE RATE IS RECORDED AS EVALUATED
-------------------------------------
It is written into the fact log at evaluation time and never re-read from the
active release when the projection is built. Reading the live release would
report *today's* rate for a case decided under a release that had a different
one -- the provenance failure `policy_version` exists to make visible. The fact
log is insert-only, so the rate a case was decided under stays recoverable even
after an operator activates a release that changes it.

WHY THESE TESTS RUN THE WORKFLOW
--------------------------------
The harness from `test_case_policy_gate` drives the real `ReturnCaseWorkflow.run`
with the real `ReturnCaseActivities`, the real deterministic evaluator and the
real Ferguson rule set loaded from `config/returns/production.yaml`. So the facts
asserted below are the ones a container would write, and the projection asserted
after them is the shipped assembler reading them back. A test that appended the
facts by hand would have passed on the day the writer was missing.

The negative case removes `seller_schedule` from the loaded policy and asserts
the original behaviour is restored *exactly*: neither fact written, both fields
`None`, applicability still travelling in `conditions`.
"""

from __future__ import annotations

import pytest

from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.operations.case_projection.assembly import project_policy_evaluation
from return_platform.policy import FeeAmountSource
from tests.policy.test_case_policy_gate import (  # reuse the established harness
    CONFIGURATION_PATH,
    NOW,
    _approvable_facts,
    _run_case,
)

pytestmark = pytest.mark.asyncio

_RATE_FACT = "policy_restocking_fee_rate_basis_points"
_SOURCE_FACT = "policy_restocking_fee_rate_source"


@pytest.fixture(scope="module")
def configuration() -> ReturnPlatformConfiguration:
    """The shipped release, policy and all.

    Declared here rather than imported from the gate module: importing a fixture
    by name shadows every helper parameter that shares it, and the two helpers
    below both take a release to modify.

    `policy_evaluation.enabled` is pinned on for the reason the gate module
    gives: the shipped file suspends the gate on this development host, and a
    deployment switch must not turn a test about what the evaluator decides into
    a test that it was skipped.
    """
    loaded = load_return_configuration(CONFIGURATION_PATH).configuration
    return loaded.model_copy(
        update={
            "policy_evaluation": loaded.policy_evaluation.model_copy(
                update={"enabled": True, "disabled_reason": None}
            )
        }
    )


def _without_seller_schedule(release: ReturnPlatformConfiguration) -> ReturnPlatformConfiguration:
    """The shipped release with one field removed.

    Copied from the real configuration rather than assembled, so the thing under
    test is a policy the loader accepts -- and so removing the schedule is the
    only difference between this and the case above it.
    """
    fee = release.return_eligibility_policy.restocking_fee.model_copy(
        update={"seller_schedule": None}
    )
    policy = release.return_eligibility_policy.model_copy(update={"restocking_fee": fee})
    return release.model_copy(update={"return_eligibility_policy": policy})


def _with_rate(
    release: ReturnPlatformConfiguration, basis_points: int
) -> ReturnPlatformConfiguration:
    """The shipped release with the seller's rate changed, as an operator would."""
    restocking = release.return_eligibility_policy.restocking_fee
    assert restocking.seller_schedule is not None
    schedule = restocking.seller_schedule.model_copy(
        update={"default_rate_basis_points": basis_points}
    )
    fee = restocking.model_copy(update={"seller_schedule": schedule})
    policy = release.return_eligibility_policy.model_copy(update={"restocking_fee": fee})
    return release.model_copy(update={"return_eligibility_policy": policy})


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------


async def test_the_evaluation_records_the_rate_and_its_authority(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """The two lines that were missing, asserted on the fact log.

    1500 basis points is the seller schedule in the shipped release, and
    `SELLER_CONFIGURATION` is the authority `SellerRestockingFeeSchedule` fixes
    it to -- a seller's own rate can never present itself as published Ferguson
    policy, which is the whole reason the source travels beside the number.

    The values are asserted as scalars because `CaseFactProjection.value` is
    typed `str | int | float | bool | None`: an enum member written here would
    parse and then fail the projection the console reads.
    """
    _outcome, harness = await _run_case(
        monkeypatch, configuration=configuration, facts=_approvable_facts()
    )

    assert harness.repository.value_of(_RATE_FACT) == 1500
    assert harness.repository.value_of(_SOURCE_FACT) == "SELLER_CONFIGURATION"
    assert isinstance(harness.repository.value_of(_SOURCE_FACT), str)
    # Beside, not instead of: applicability still travels where it always did.
    assert harness.repository.value_of("policy_restocking_fee_applies") is True
    assert harness.repository.value_of("policy_restocking_fee_waived") is False


async def test_the_rate_reaches_the_projection_through_the_shipped_assembler(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """End to end: evaluator -> fact log -> `PolicyEvaluationProjection`.

    Read back through `latest_case_facts`, the same projection
    `CaseRepository.load_case_projection_state` builds, so the assertion is what
    the fee pane would actually render.
    """
    _outcome, harness = await _run_case(
        monkeypatch, configuration=configuration, facts=_approvable_facts()
    )

    evaluation = project_policy_evaluation(
        await harness.repository.latest_case_facts("case-under-test")
    )

    assert evaluation is not None
    assert evaluation.rateBasisPoints == 1500
    assert evaluation.rateSource is FeeAmountSource.SELLER_CONFIGURATION


async def test_the_rate_is_the_one_evaluated_and_not_the_one_configured_now(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """The provenance property, stated as a passing test.

    A case is decided under the shipped release. The operator then activates a
    release whose seller schedule says something else. The projection must still
    report what the case was decided under -- reading the live release here
    would silently restate the case's history every time an operator changed a
    number.
    """
    _outcome, decided = await _run_case(
        monkeypatch, configuration=configuration, facts=_approvable_facts()
    )
    facts = await decided.repository.latest_case_facts("case-under-test")

    # The operator activates a release that halves the rate. A second case
    # decided under it records 750, which proves the new release is genuinely
    # live and that the first assertion below is not simply reading a constant.
    _outcome, later = await _run_case(
        monkeypatch, configuration=_with_rate(configuration, 750), facts=_approvable_facts()
    )
    assert later.repository.value_of(_RATE_FACT) == 750

    # The first case still reports what it was decided under. It has to: the
    # assembler is handed the log and nothing else, so there is no release for
    # it to ask.
    evaluation = project_policy_evaluation(facts)
    assert evaluation is not None
    assert evaluation.rateBasisPoints == 1500
    assert evaluation.evaluatedAt == NOW


# ---------------------------------------------------------------------------
# No schedule, no rate -- the original behaviour, exactly
# ---------------------------------------------------------------------------


async def test_a_policy_with_no_seller_schedule_writes_neither_fact(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """Removing the schedule restores what the platform did before this change.

    `_seller_restocking_fee` produces a `FeeDetermination` with no rate,
    `_append_policy_facts` drops the `None`s, and the log holds neither fact --
    not a fact holding null, which would be a different statement and would
    reach `_restocking_rate` as a half-written pair.
    """
    _outcome, harness = await _run_case(
        monkeypatch,
        configuration=_without_seller_schedule(configuration),
        facts=_approvable_facts(),
    )

    names = {str(fact["factName"]) for fact in harness.repository.facts}
    assert _RATE_FACT not in names
    assert _SOURCE_FACT not in names
    # The evaluation still happened, and applicability is still recorded.
    assert harness.repository.value_of("policy_decision") == "APPROVE"
    assert harness.repository.value_of("policy_restocking_fee_applies") is True


async def test_a_policy_with_no_seller_schedule_projects_no_rate(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """And the pane has applicability and nothing else, exactly as before."""
    _outcome, harness = await _run_case(
        monkeypatch,
        configuration=_without_seller_schedule(configuration),
        facts=_approvable_facts(),
    )

    evaluation = project_policy_evaluation(
        await harness.repository.latest_case_facts("case-under-test")
    )

    assert evaluation is not None
    assert evaluation.rateBasisPoints is None
    assert evaluation.rateSource is None
    assert evaluation.conditions is not None
    assert "RESTOCKING_FEE_APPLIES" in [condition.value for condition in evaluation.conditions]


# ---------------------------------------------------------------------------
# The pair is never half-written
# ---------------------------------------------------------------------------


async def test_a_waived_fee_carries_no_rate_because_no_fee_applies(
    monkeypatch: pytest.MonkeyPatch, configuration: ReturnPlatformConfiguration
) -> None:
    """`FeeDetermination` refuses a rate on a fee that does not apply, and the
    recorder cannot get round it: the waiver removes the rate at the source, so
    no fact is written and the projection reports a waived fee with no
    percentage rather than a percentage nobody owes."""
    facts = [*_approvable_facts()]
    facts.append(
        {
            "factId": "fact-seller_fee_waiver",
            "caseId": "case-under-test",
            "factName": "seller_fee_waiver",
            "value": True,
            "acquisitionMethod": "STATED",
            "sourceSystem": "CONVERSATION",
            "sourcePath": "CONVERSATION_MESSAGE",
            "supersedesFactId": None,
            "observedAt": NOW,
            "recordedAt": NOW,
        }
    )

    _outcome, harness = await _run_case(monkeypatch, configuration=configuration, facts=facts)

    assert harness.repository.value_of("policy_restocking_fee_waived") is True
    assert harness.repository.value_of("policy_restocking_fee_applies") is False
    names = {str(fact["factName"]) for fact in harness.repository.facts}
    assert _RATE_FACT not in names
    assert _SOURCE_FACT not in names

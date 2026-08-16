"""The return-window policy is released configuration, not code.

The operator's second requirement -- "policy should be in graph and configurable
/ no hardcoded" -- is a claim about the deployed system, so these tests exercise
the actual packaged `config/returns/production.yaml` and the actual transport
the runtime uses, rather than a policy built in Python.

The transport is `ConfigurationSnapshotBuilder.build_snapshot`: it reads the
`RETURN_PLATFORM` domain payload off the active `ConfigurationRelease` in Neo4j
and revives it with `ReturnPlatformConfiguration.model_validate`. Neo4j stores
that payload as a JSON string on `ConfigurationDomain.payload_json`, so the
round trip a policy value survives is exactly
`model_dump(mode="json") -> json.dumps -> json.loads -> model_validate`. That is
what is reproduced below; no database is needed to prove the value crosses it
intact, and needing one would make this a test nobody runs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from return_platform.configuration.graph_repository import compute_release_checksum
from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    load_return_configuration,
    validate_return_eligibility_policy,
)
from return_platform.configuration.snapshot import RETURN_PLATFORM_DOMAIN_KEY
from return_platform.policy import (
    EligibilityDecision,
    PolicyClock,
    PolicyEvaluationInput,
    PolicyReasonCode,
    ReturnWindowBasis,
    UnstatedConditionFacts,
    evaluate_return_eligibility,
)
from return_platform.workflows.case_policy_facts import purchase_date_from_confirmed_order

_BACKEND = Path(__file__).resolve().parents[2]
PRODUCTION_YAML = _BACKEND / "config" / "returns" / "production.yaml"
_DATASET = _BACKEND / "fixtures" / "reference_dataset" / "salesInv1.json"

NEW_YORK = ZoneInfo("America/New_York")


@pytest.fixture(scope="module")
def packaged() -> ReturnPlatformConfiguration:
    return load_return_configuration(PRODUCTION_YAML).configuration


def _through_the_graph(
    configuration: ReturnPlatformConfiguration,
) -> ReturnPlatformConfiguration:
    """The release payload's exact journey through Neo4j and back."""
    payload_json = json.dumps(
        configuration.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    domain_payloads = {RETURN_PLATFORM_DOMAIN_KEY: json.loads(payload_json)}
    return ReturnPlatformConfiguration.model_validate(
        domain_payloads[RETURN_PLATFORM_DOMAIN_KEY]
    )


# ---------------------------------------------------------------------------
# The values are in the release
# ---------------------------------------------------------------------------


def test_the_packaged_release_declares_the_window(packaged: ReturnPlatformConfiguration) -> None:
    policy = validate_return_eligibility_policy(packaged)

    assert policy.standard_stock_return.purchase_window.days == 30
    assert policy.standard_stock_return.purchase_window.basis is ReturnWindowBasis.PURCHASE_DATE


def test_the_packaged_release_declares_the_narrowing(
    packaged: ReturnPlatformConfiguration,
) -> None:
    policy = validate_return_eligibility_policy(packaged)

    assert (
        policy.standard_stock_return.unstated_condition_facts
        is UnstatedConditionFacts.NOT_EVALUATED
    )


def test_the_packaged_release_declares_what_happens_past_the_window(
    packaged: ReturnPlatformConfiguration,
) -> None:
    policy = validate_return_eligibility_policy(packaged)

    assert policy.outside_standard_window.decision is EligibilityDecision.REVIEW_REQUIRED
    assert (
        policy.outside_standard_window.reason_code
        is PolicyReasonCode.OUTSIDE_STANDARD_RETURN_WINDOW
    )


def test_the_packaged_release_binds_where_the_order_date_lives(
    packaged: ReturnPlatformConfiguration,
) -> None:
    """The window's basis is a source binding, declared beside the others.

    No physical path is written in the policy package or in the fact adapter, so
    re-binding the field is an operator edit rather than a code change.
    """
    paths = packaged.source_resolution.order_date_paths

    assert paths == ("salesHdr.salesHdrData.orderDate",)


def test_the_bound_path_resolves_the_real_order(
    packaged: ReturnPlatformConfiguration,
) -> None:
    """The binding and the data are checked against each other, not assumed."""
    orders = json.loads(_DATASET.read_text(encoding="utf-8"))
    order = next(record for record in orders if record["_id"] == "CHARLOTTE*CQ363350")

    resolved = purchase_date_from_confirmed_order(
        order, paths=packaged.source_resolution.order_date_paths
    )

    assert resolved is not None
    assert resolved.astimezone(NEW_YORK).date() == datetime(2025, 10, 14).date()


# ---------------------------------------------------------------------------
# ...and they survive the graph
# ---------------------------------------------------------------------------


def test_every_policy_value_survives_the_neo4j_release_payload(
    packaged: ReturnPlatformConfiguration,
) -> None:
    """A value that did not round-trip would be a policy the runtime never sees.

    Compared as a whole rather than field by field, so a block added to the
    policy later is covered by this test on the day it is added.
    """
    revived = _through_the_graph(packaged)

    assert revived.return_eligibility_policy == packaged.return_eligibility_policy
    assert revived.source_resolution.order_date_paths == (
        packaged.source_resolution.order_date_paths
    )


def test_the_policy_is_inside_what_the_release_checksum_covers(
    packaged: ReturnPlatformConfiguration,
) -> None:
    """Changing the window changes the release identity.

    `build_snapshot` recomputes this checksum and refuses a release whose stored
    value disagrees, so a policy value edited underneath a published release
    cannot be served quietly.
    """
    payload = packaged.model_dump(mode="json")
    tampered = json.loads(json.dumps(payload))
    tampered["return_eligibility_policy"]["standard_stock_return"]["purchase_window"][
        "days"
    ] = 60

    original = compute_release_checksum(
        [(RETURN_PLATFORM_DOMAIN_KEY, json.dumps(payload, sort_keys=True))]
    )
    changed = compute_release_checksum(
        [(RETURN_PLATFORM_DOMAIN_KEY, json.dumps(tampered, sort_keys=True))]
    )

    assert original != changed


# ---------------------------------------------------------------------------
# ...and changing them is an edit, not a deployment
# ---------------------------------------------------------------------------


def _decide(
    configuration: ReturnPlatformConfiguration, *, purchased: datetime, requested: datetime
) -> EligibilityDecision | None:
    policy = validate_return_eligibility_policy(configuration)
    facts = PolicyEvaluationInput(request_date=requested, purchase_date=purchased)
    return evaluate_return_eligibility(
        policy, facts, PolicyClock(evaluated_at=requested, local_zone=NEW_YORK)
    ).decision


def test_widening_the_window_is_a_yaml_edit_and_nothing_else(
    tmp_path: Path, packaged: ReturnPlatformConfiguration
) -> None:
    """30 to 60, with no code between the edit and the decision.

    The same case is reviewed by the packaged release and approved by the edited
    one. Nothing in the evaluator, the adapter or this test knows the number.
    """
    edited_path = tmp_path / "production.yaml"
    edited_path.write_text(
        PRODUCTION_YAML.read_text(encoding="utf-8").replace(
            "purchase_window: { days: 30, basis: PURCHASE_DATE }",
            "purchase_window: { days: 60, basis: PURCHASE_DATE }",
        ),
        encoding="utf-8",
    )
    edited = load_return_configuration(edited_path).configuration

    purchased = datetime(2025, 10, 14, 12, 0, tzinfo=UTC)
    requested = datetime(2025, 11, 25, 12, 0, tzinfo=UTC)  # day 42

    assert edited.return_eligibility_policy is not None
    assert edited.return_eligibility_policy.standard_stock_return.purchase_window.days == 60
    assert _decide(packaged, purchased=purchased, requested=requested) is (
        EligibilityDecision.REVIEW_REQUIRED
    )
    assert _decide(edited, purchased=purchased, requested=requested) is (
        EligibilityDecision.APPROVE
    )
    # And the widened window is what the graph would carry, not just what the
    # file says.
    assert (
        _through_the_graph(edited).return_eligibility_policy.standard_stock_return.purchase_window.days
        == 60
    )


def test_restoring_the_other_checks_is_a_yaml_edit_and_nothing_else(
    tmp_path: Path, packaged: ReturnPlatformConfiguration
) -> None:
    """The narrowing is reversible on the same terms it was applied.

    The rules and their tests were never removed; one released value stands them
    down and the same value brings them back.
    """
    restored_path = tmp_path / "production.yaml"
    restored_path.write_text(
        PRODUCTION_YAML.read_text(encoding="utf-8").replace(
            "unstated_condition_facts: NOT_EVALUATED",
            "unstated_condition_facts: REVIEW_REQUIRED",
        ),
        encoding="utf-8",
    )
    restored = load_return_configuration(restored_path).configuration

    purchased = datetime(2025, 10, 14, 12, 0, tzinfo=UTC)
    requested = datetime(2025, 11, 1, 12, 0, tzinfo=UTC)  # day 18, in window

    assert _decide(packaged, purchased=purchased, requested=requested) is (
        EligibilityDecision.APPROVE
    )
    assert _decide(restored, purchased=purchased, requested=requested) is (
        EligibilityDecision.REVIEW_REQUIRED
    )


def test_an_absent_policy_refuses_activation_rather_than_reviewing_everything(
    packaged: ReturnPlatformConfiguration,
) -> None:
    """The failure mode the graph-resident design must not have.

    A release that carries no policy is an operational fault, and it has to look
    like one. Degrading to `REVIEW_REQUIRED` would present an unpublished rule
    set as an evaluator working correctly and cautiously.
    """
    without = packaged.model_copy(update={"return_eligibility_policy": None})

    with pytest.raises(ValueError, match="publish a policy release"):
        validate_return_eligibility_policy(without)

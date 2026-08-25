"""The Support Response Agent issues what the method requires, and only that."""

from pathlib import Path

from return_platform.agents.contracts import (
    SupportHandoffItemInput,
    SupportResponseRequest,
)
from return_platform.agents.registry import AgentRegistry
from return_platform.configuration.return_configuration import load_return_configuration

CONFIG = Path(__file__).resolve().parents[2] / "config" / "returns" / "production.yaml"


def agent():
    return AgentRegistry.build(load_return_configuration(CONFIG).configuration).support_response


def _request(**overrides) -> SupportResponseRequest:
    base = dict(
        caseId="case-42",
        workItemId="wi-1",
        orderReference="CQ800002",
        customerName="Northgate Plumbing",
        returnMethod="PREPAID_PARCEL",
        bayReference="BAY-7",
        items=(
            SupportHandoffItemInput(
                lineReference="10",
                productName="Chrome Faucet",
                sku="F-100",
                quantity=2,
                reason="DAMAGED",
                condition="Damaged",
            ),
        ),
    )
    base.update(overrides)
    return SupportResponseRequest(**base)


def test_parcel_method_gets_rma_tracking_and_label() -> None:
    result = agent().assess(_request())
    assert result.ready is True
    plan = result.plan
    assert plan is not None
    assert plan.returnReference == "RMA-CASE42"
    assert plan.trackingReference is not None
    assert plan.labelReference is not None
    assert plan.carrier == "UPS"
    assert plan.orderLineReferences == ("10",)
    # DAMAGED is a photo-required reason, so the instructions say to photograph.
    assert "Photograph" in plan.instructions
    assert "BAY-7" in plan.instructions
    assert result.decision.decision == "RETURN_PLANNED"


def test_rma_only_method_issues_no_shipping_artifacts() -> None:
    result = agent().assess(_request(returnMethod="CUSTOMER_KEEP"))
    plan = result.plan
    assert plan is not None
    assert plan.trackingReference is None
    assert plan.labelReference is None
    assert plan.shippingInstructionReference is None
    assert plan.carrier is None


def test_unknown_method_blocks_instead_of_inventing() -> None:
    result = agent().assess(_request(returnMethod=None))
    assert result.ready is False
    assert "return_method" in result.missingFields
    assert result.plan is None
    assert result.clarificationRequest is not None
    assert result.decision.decision == "CLARIFICATION_REQUIRED"


def test_freight_method_requires_a_return_location() -> None:
    result = agent().assess(
        _request(returnMethod="OFFSITE_LTL", bayReference=None, returnLocation=None)
    )
    assert result.ready is False
    assert "return_location" in result.missingFields


def test_retry_plans_identical_references() -> None:
    first = agent().assess(_request())
    second = agent().assess(_request())
    assert first.plan is not None and second.plan is not None
    assert first.plan == second.plan

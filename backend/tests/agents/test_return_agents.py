from pathlib import Path

from return_platform.agents.contracts import (
    BayAssessmentRequest,
    BayCandidateInput,
    DiscoveryAssessmentRequest,
    DiscoveryCandidateInput,
    FeedbackAssessmentRequest,
    FulfillmentAssessmentRequest,
    FulfillmentFact,
    NormalizedReturnMethod,
    OrderSource,
    ProductPresence,
    ReturnItemInput,
    ReturnWorkflowAssessmentRequest,
)
from return_platform.agents.registry import ReturnAgentRegistry
from return_platform.configuration.return_configuration import load_return_configuration

CONFIG = Path(__file__).resolve().parents[2] / "config" / "returns" / "production.yaml"


def registry() -> ReturnAgentRegistry:
    return ReturnAgentRegistry.build(load_return_configuration(CONFIG).configuration)


def test_configuration_loads_all_six_agents() -> None:
    loaded = load_return_configuration(CONFIG)
    assert set(loaded.configuration.agents) == {
        "order_discovery",
        "return_workflow",
        "return_fulfillment",
        "bay_assignment",
        "feedback_learning",
        "order_analysis",
    }
    assert loaded.configuration.discovery.auto_confirmation_allowed is False
    assert loaded.configuration.omc.rga_is_customer_return is False


def test_order_discovery_requires_confirmation_and_detects_web_order() -> None:
    result = registry().order_discovery.assess(
        DiscoveryAssessmentRequest(
            suppliedEvidence={"order_number": "W1234567890"},
            candidates=(
                DiscoveryCandidateInput(
                    candidateId="one",
                    orderReference="1001",
                    customerReference="C1",
                    matchedAnchors=("ORDER_NUMBER",),
                    evidenceReferences=("SOURCE:ONE",),
                ),
            ),
        )
    )
    assert result.orderSource is OrderSource.FERGUSONHOME_WEB
    assert result.confirmationRequired is True
    assert result.ambiguous is False
    assert result.rankedCandidates[0].scoreMillionths == 1_000_000


def test_return_workflow_requires_damage_photo() -> None:
    result = registry().return_workflow.assess(
        ReturnWorkflowAssessmentRequest(
            sessionId="session",
            orderSource=OrderSource.FERGUSON_BRANCH,
            productPresence=ProductPresence.PRESENT_AT_BRANCH,
            proposedReturnMethod=NormalizedReturnMethod.BRANCH_UPS,
            branchId="BR-1",
            associateId="associate",
            items=(
                ReturnItemInput(
                    orderLineId="line-1",
                    productId="product-1",
                    requestedQuantity=1,
                    shippedQuantity=1,
                    reasonCode="DAMAGED",
                ),
            ),
        )
    )
    assert result.complete is False
    assert result.photoEvidenceRequired is True
    assert "required_photo_evidence" in result.missingFields


def test_fulfillment_never_treats_tender_as_pickup() -> None:
    result = registry().return_fulfillment.assess(
        FulfillmentAssessmentRequest(
            returnVersion="V2",
            rawReturnStatus="ISSUED",
            facts=(
                FulfillmentFact(
                    factType="BOL_TENDERED",
                    status="TENDERED",
                    evidenceReference="OMC:BOL:1",
                ),
            ),
        )
    )
    assert result.physicalReturnComplete is False
    assert result.nextExpectedEvent == "CARRIER_BOOKING"
    assert "BOL_TENDERED_NOT_BOOKED" in result.decision.warnings


def test_bay_agent_blocks_before_receipt() -> None:
    result = registry().bay_assignment.assess(
        BayAssessmentRequest(
            physicalStatus="IN_TRANSIT",
            returnMethod=NormalizedReturnMethod.BRANCH_LTL,
            requiredCapacity=1,
            candidates=(
                BayCandidateInput(
                    bayId="BAY-1",
                    bayType="LTL",
                    capacityAvailable=10,
                    supportedReturnMethods=(NormalizedReturnMethod.BRANCH_LTL,),
                ),
            ),
        )
    )
    assert result.recommendedBayId is None
    assert result.decision.decision == "NOT_READY"


def test_feedback_agent_only_recommends_reviewed_changes() -> None:
    result = registry().feedback_learning.assess(
        FeedbackAssessmentRequest(
            sessionId="session",
            metrics={"supportClarificationCount": 3, "assumptionMismatch": True},
            evidenceReferences=("EVENT:1",),
        )
    )
    assert result.reviewRequired is True
    assert len(result.recommendations) == 2
    assert result.decision.decision == "REVIEW_REQUIRED"

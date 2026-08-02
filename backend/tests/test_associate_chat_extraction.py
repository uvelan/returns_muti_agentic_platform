from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.conversation.progressive import (
    ConversationStatePolicy,
    DisambiguationRule,
    ProgressiveConversationEngine,
)
from return_platform.operations.associate_flow import (
    AnchorType,
    AssociateConversationService,
    AssociateConversationView,
    ConfirmDiscoveryRequest,
    DiscoveryLock,
    OrderCandidate,
    OrderLineCandidate,
    StartAssociateConversationRequest,
    _is_expired,
    _normalize_utc_datetime,
    redact_ambiguous_candidates,
)
from return_platform.operations.models import AIDecision

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def service() -> Any:
    instance = cast(Any, object.__new__(AssociateConversationService))
    instance._return_configuration = load_return_configuration(
        BACKEND_ROOT / "config" / "returns" / "production.yaml"
    ).configuration
    progressive = instance._return_configuration.discovery.progressive
    instance._progressive_conversation = ProgressiveConversationEngine(
        rules=tuple(
            DisambiguationRule(
                slot=item.slot,
                candidate_field=item.candidate_field,
                label=item.label,
                priority=item.priority,
            )
            for item in progressive.disambiguation_attributes
        ),
        candidate_ttl_seconds=progressive.candidate_ttl_seconds,
        max_clarification_options=progressive.max_clarification_options,
        states=ConversationStatePolicy(
            no_candidates=progressive.dialogue_states.no_candidates,
            single_candidate=progressive.dialogue_states.single_candidate,
            slot_disambiguation=progressive.dialogue_states.slot_disambiguation,
            generic_disambiguation=progressive.dialogue_states.generic_disambiguation,
        ),
    )
    return instance


class SmartQuestionGateway:
    def __init__(
        self,
        explanation: str | None,
        *,
        confidence: int = 900_000,
        fallback_used: bool = False,
    ) -> None:
        self.explanation = explanation
        self.confidence = confidence
        self.fallback_used = fallback_used
        self.calls: list[dict[str, Any]] = []

    async def evaluate(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            pending_interception=False,
            trace=SimpleNamespace(
                fallbackUsed=self.fallback_used,
                decision=AIDecision.REVIEW_REQUIRED,
                explanation=self.explanation,
                confidenceMillionths=self.confidence,
            ),
        )


def conversation_without_candidates() -> AssociateConversationView:
    now = datetime.now(UTC)
    return AssociateConversationView(
        id="conversation-smart-question",
        status="NO_MATCH",
        anchorType=AnchorType.PRODUCT_DESCRIPTION,
        anchorValueMasked="pressure valve",
        messages=[],
        candidates=[],
        version=0,
        createdAt=now,
        updatedAt=now,
    )


def test_chat_extracts_configured_strong_anchor_from_raw_sentence() -> None:
    extracted = service()._extract_anchor(
        "The customer says order SO-00010001 arrived damaged yesterday."
    )
    assert extracted.anchorType is AnchorType.ORDER_NUMBER
    assert extracted.anchorValue == "SO-00010001"


def test_chat_prefers_configured_strong_anchor_when_message_has_multiple_clues() -> None:
    extracted = service()._extract_anchor(
        "SKU-123456 is on order ORD-100001 and needs to be returned."
    )
    assert extracted.anchorType is AnchorType.ORDER_NUMBER
    assert extracted.anchorValue == "ORD-100001"


def test_chat_uses_product_description_when_no_structured_anchor_is_present() -> None:
    message = "The brass faucet from the showroom is leaking."
    extracted = service()._extract_anchor(message)
    assert extracted.anchorType is AnchorType.PRODUCT_DESCRIPTION
    assert extracted.anchorValue == message


def test_chat_extracts_and_normalizes_lowercase_order_number() -> None:
    extracted = service()._extract_anchor("can you check w000000001 please")
    assert extracted.anchorType is AnchorType.ORDER_NUMBER
    assert extracted.anchorValue == "W000000001"


def test_fuzzy_query_removes_conversation_noise_and_keeps_typo_tolerance() -> None:
    query = service()._fuzzy_query(
        "Find the customer named Jhn Smtih who bought a cordles dril last week.",
        require_all=False,
    )

    assert query == "Jhn OR Smtih~1 OR cordles~1 OR dril~1"
    assert "customer" not in query.lower()
    assert "named" not in query.lower()


def test_fuzzy_query_for_customer_name_requires_all_name_tokens() -> None:
    assert service()._fuzzy_query("name Demo Customer") == "Demo~1"


def test_clarification_prompt_exposes_only_the_selected_field_values() -> None:
    candidates = [
        OrderCandidate(
            customerReference=f"CUST-{index}",
            customerName=name,
            orderReference=f"ORD-{index}",
            orderStatus="DELIVERED",
            confidenceMillionths=500_000,
            evidenceSource="TEST",
            lines=[
                OrderLineCandidate(
                    orderLineId=f"LINE-{index}",
                    productId=f"PRODUCT-{index}",
                    productDescription=product,
                )
            ],
        )
        for index, (name, product) in enumerate(
            (
                ("Maya Foster", "Safety Sensor"),
                ("Maya Foster", "Pump Controller"),
                ("Nadia Diaz", "Safety Sensor"),
            ),
            start=1,
        )
    ]

    prompt = service()._clarification_prompt(
        candidates,
        ["customer_name"],
        "Which customer do you mean?",
    )

    assert prompt is not None
    assert prompt.slot == "customer_name"
    assert [(option.value, option.candidateCount) for option in prompt.options] == [
        ("Maya Foster", 2),
        ("Nadia Diaz", 1),
    ]


def test_typo_first_name_requests_possible_full_name_without_revealing_orders() -> None:
    candidates = [
        OrderCandidate(
            customerReference=f"CUST-{index}",
            customerName=f"Noah {last_name}",
            orderReference=f"SO-2026-{index:07d}",
            orderStatus="DELIVERED",
            confidenceMillionths=500_000,
            evidenceSource="TEST",
            lines=[
                OrderLineCandidate(
                    orderLineId=f"LINE-{index}",
                    productId=f"PRODUCT-{index}",
                    productDescription="Faucet",
                )
            ],
        )
        for index, last_name in enumerate(
            (
                "Smith",
                "Lewis",
                "Carter",
                "Singh",
                "Brown",
                "Wilson",
                "Garcia",
                "Martin",
                "Taylor",
            ),
            start=1,
        )
    ]
    instance = service()

    state, requested_slots, _set_id, _expires_at, question = (
        instance._dialogue_projection(candidates)
    )
    prompt = instance._clarification_prompt(candidates, requested_slots, question)

    assert state == "CUSTOMER_DISAMBIGUATION"
    assert requested_slots == ["customer_name"]
    assert question == "Which customer are you looking for?"
    assert prompt is not None
    assert [option.label for option in prompt.options] == [
        "Noah Brown",
        "Noah Carter",
        "Noah Garcia",
        "Noah Lewis",
        "Noah Martin",
        "Noah Singh",
        "Noah Smith",
        "Noah Taylor",
        "Noah Wilson",
    ]
    assert all("SO-" not in option.label for option in prompt.options)


def test_ambiguous_order_details_are_redacted_from_public_response() -> None:
    now = datetime.now(UTC)
    candidates = [
        OrderCandidate(
            customerReference=f"CUST-{index}",
            customerName=f"Noah {index}",
            orderReference=f"ORD-{index}",
            confidenceMillionths=500_000,
            evidenceSource="TEST",
            lines=[
                OrderLineCandidate(
                    orderLineId=f"LINE-{index}",
                    productId=f"PRODUCT-{index}",
                )
            ],
        )
        for index in (1, 2)
    ]
    conversation = AssociateConversationView(
        id="conversation-redacted",
        status="DISCOVERY_CLARIFICATION_REQUIRED",
        anchorType=AnchorType.CUSTOMER_NAME,
        anchorValueMasked="Naoh",
        messages=[],
        candidates=candidates,
        discoveryAssessment={
            "rankedCandidates": [
                {"candidateId": "CUST-1:ORD-1", "orderReference": "ORD-1"}
            ]
        },
        version=0,
        createdAt=now,
        updatedAt=now,
    )

    public = redact_ambiguous_candidates(conversation)

    assert public.candidates == []
    assert public.discoveryAssessment is None
    assert conversation.candidates == candidates
    assert conversation.discoveryAssessment is not None


def test_smart_question_configuration_contains_ported_goal_policy() -> None:
    policy = service()._return_configuration.clarification_policy

    assert policy.field_selection_owner == "LLM"
    assert "DISAMBIGUATE_MULTIPLE_ORDERS" in policy.goals
    assert {"invoice_number", "customer_po_number", "approximate_purchase_date"} <= {
        item.field for item in policy.fields
    }
    assert all(not hasattr(item, "question") for item in policy.fields)


@pytest.mark.asyncio
async def test_llm_can_choose_any_consumable_field_from_configured_goal() -> None:
    instance = service()
    gateway = SmartQuestionGateway(
        '{"field":"email","question":"Which email address was used for this order?"}'
    )
    instance._ai = gateway

    question = await instance._generate_smart_question(conversation_without_candidates())

    assert question == "Which email address was used for this order?"
    call = gateway.calls[0]
    assert call["task_id"] == "RETURN_CLARIFICATION_FIELD_V2"
    assert call["redacted_input"]["goal"] == "IDENTIFY_ORDER"
    allowed = {item["field"] for item in call["redacted_input"]["allowedFields"]}
    assert "email" in allowed
    assert "order_number" in allowed
    assert "invoice_number" not in allowed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "explanation",
    (
        '{"field":"bank_account","question":"What is the bank account number?"}',
        '{"field":"email","question":"Tell me your email"}',
        '{"field":"email","question":"What email was used?","extra":true}',
        "What email was used?",
    ),
)
async def test_unconfigured_or_invalid_llm_question_uses_configured_fallback(
    explanation: str,
) -> None:
    instance = service()
    instance._ai = SmartQuestionGateway(explanation)

    question = await instance._generate_smart_question(conversation_without_candidates())

    assert question == "Could you provide the Ferguson order number or web order number?"


@pytest.mark.asyncio
async def test_gateway_fallback_cannot_replace_configured_question_policy() -> None:
    instance = service()
    instance._ai = SmartQuestionGateway(
        '{"field":"email","question":"What email was used?"}',
        fallback_used=True,
    )

    question = await instance._generate_smart_question(conversation_without_candidates())

    assert question == "Could you provide the Ferguson order number or web order number?"


@pytest.mark.asyncio
async def test_multi_anchor_search_retains_only_orders_matching_every_anchor() -> None:
    instance = service()

    def candidate(customer: str, order: str, product: str) -> OrderCandidate:
        return OrderCandidate(
            customerReference=customer,
            customerName=customer,
            orderReference=order,
            confidenceMillionths=500_000,
            evidenceSource="TEST",
            lines=[
                OrderLineCandidate(
                    orderLineId=f"{order}:1",
                    productId=product,
                    productDescription=product,
                )
            ],
        )

    by_anchor = {
        AnchorType.CUSTOMER_NAME: [
            candidate("Enmen", "SO-1", "faucet"),
            candidate("Enmen", "SO-2", "valve"),
        ],
        AnchorType.PRODUCT_DESCRIPTION: [
            candidate("Enmen", "SO-1", "faucet"),
            candidate("Other", "SO-3", "faucet"),
        ],
    }

    async def discover(anchor_type: AnchorType, _anchor_value: str) -> list[OrderCandidate]:
        return by_anchor[anchor_type]

    instance._discover_candidates = discover
    results = await instance._discover_candidates_for_anchors(
        (
            StartAssociateConversationRequest(
                anchorType=AnchorType.CUSTOMER_NAME,
                anchorValue="Enmen",
            ),
            StartAssociateConversationRequest(
                anchorType=AnchorType.PRODUCT_DESCRIPTION,
                anchorValue="faucet",
            ),
        )
    )

    assert [(item.customerReference, item.orderReference) for item in results] == [
        ("Enmen", "SO-1")
    ]
    assert results[0].evidenceSource == "MULTI_ANCHOR_INTERSECTION"


def test_candidate_expiry_accepts_naive_mongodb_datetimes() -> None:
    assert _is_expired(datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1))


def test_mongodb_datetimes_are_serialized_as_utc() -> None:
    normalized = _normalize_utc_datetime(datetime(2026, 7, 28, 10, 53, 16))

    assert normalized is not None
    assert normalized.tzinfo is UTC
    assert normalized.isoformat() == "2026-07-28T10:53:16+00:00"


@pytest.mark.asyncio
async def test_confirm_is_idempotent_after_conversation_version_advances() -> None:
    now = datetime.now(UTC)
    conversation = AssociateConversationView(
        id="conversation-1",
        status="DETAILS_REQUIRED",
        anchorType=AnchorType.CUSTOMER_NAME,
        anchorValueMasked="Maya",
        messages=[],
        candidates=[],
        discoveryLock=DiscoveryLock(
            customerReference="CUST-1",
            orderReference="ORD-1",
            orderLineId="ORD-1:LINE:1",
            productId="PRODUCT-1",
            lockDigest="0" * 64,
            confirmedBy="associate-1",
            confirmedAt=now,
        ),
        version=6,
        createdAt=now,
        updatedAt=now,
    )
    instance = service()

    async def get_conversation(_conversation_id: str) -> AssociateConversationView:
        return conversation

    instance.get = get_conversation

    result = await instance.confirm(
        conversation.id,
        ConfirmDiscoveryRequest(
            candidateIndex=0,
            orderLineId="ORD-1:LINE:1",
            expectedVersion=5,
        ),
        actor_id="associate-1",
    )

    assert result is conversation


@pytest.mark.asyncio
async def test_confirm_rejects_ambiguous_candidate_set() -> None:
    now = datetime.now(UTC)

    def candidate(index: int) -> OrderCandidate:
        return OrderCandidate(
            customerReference=f"CUST-{index}",
            customerName=f"Noah {index}",
            orderReference=f"ORD-{index}",
            confidenceMillionths=500_000,
            evidenceSource="TEST",
            lines=[
                OrderLineCandidate(
                    orderLineId=f"LINE-{index}",
                    productId=f"PRODUCT-{index}",
                )
            ],
        )

    conversation = AssociateConversationView(
        id="conversation-ambiguous",
        status="DISCOVERY_CLARIFICATION_REQUIRED",
        anchorType=AnchorType.CUSTOMER_NAME,
        anchorValueMasked="Naoh",
        messages=[],
        candidates=[candidate(1), candidate(2)],
        version=1,
        createdAt=now,
        updatedAt=now,
    )
    instance = service()

    async def get_conversation(_conversation_id: str) -> AssociateConversationView:
        return conversation

    instance.get = get_conversation

    with pytest.raises(ValueError, match="exactly one candidate"):
        await instance.confirm(
            conversation.id,
            ConfirmDiscoveryRequest(
                candidateIndex=0,
                orderLineId="LINE-1",
                expectedVersion=1,
            ),
            actor_id="associate-1",
        )

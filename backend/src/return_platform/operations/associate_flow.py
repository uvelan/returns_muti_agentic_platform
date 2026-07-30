"""Associate-first conversational discovery, confirmation lock, and return handoff."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, cast

from neo4j import AsyncDriver
from neo4j.exceptions import Neo4jError, ServiceUnavailable, SessionExpired
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

from return_platform.agents.contracts import (
    DiscoveryAssessmentRequest,
    DiscoveryCandidateInput,
    NormalizedReturnMethod,
    OrderSource,
    ProductPresence,
    ReturnItemInput,
    ReturnWorkflowAssessmentRequest,
)
from return_platform.agents.order_discovery import OrderDiscoveryAgent
from return_platform.agents.return_workflow import ReturnWorkflowAgent
from return_platform.ai_gateway.service import AIGatewayRepository, AIGatewayService
from return_platform.configuration.return_configuration import (
    ReturnPlatformConfiguration,
    load_return_configuration,
)
from return_platform.configuration.settings import Settings
from return_platform.conversation.progressive import (
    ConversationStatePolicy,
    DisambiguationRule,
    ProgressiveConversationEngine,
)
from return_platform.operations.models import AIDecision, ReturnCreateRequest, ReturnSessionView
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.return_support.service import (
    CreateSupportWorkItemRequest,
    ReturnSupportService,
)
from return_platform.security.contact_evidence import contact_lookup_digest

logger = logging.getLogger("return_platform.operations.associate_flow")


def _normalize_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


_FUZZY_STOP_WORDS = {
    "a",
    "an",
    "and",
    "bought",
    "customer",
    "find",
    "for",
    "have",
    "i",
    "is",
    "last",
    "looking",
    "name",
    "named",
    "order",
    "return",
    "the",
    "this",
    "to",
    "week",
    "who",
    "with",
}
_DISCOVERY_INTENT_CONFIDENCE_THRESHOLD = 700_000


class AssociateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AnchorType(StrEnum):
    ORDER_NUMBER = "ORDER_NUMBER"
    CUSTOMER_ID = "CUSTOMER_ID"
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    TRACKING_NUMBER = "TRACKING_NUMBER"
    SKU = "SKU"
    CUSTOMER_NAME = "CUSTOMER_NAME"
    PRODUCT_DESCRIPTION = "PRODUCT_DESCRIPTION"


_PROTECTED_ANCHOR_TYPES = {
    AnchorType.ORDER_NUMBER,
    AnchorType.CUSTOMER_ID,
    AnchorType.PHONE,
    AnchorType.EMAIL,
    AnchorType.TRACKING_NUMBER,
    AnchorType.SKU,
}


class ConversationMessage(AssociateModel):
    id: str
    role: str
    content: str
    createdAt: datetime

    @field_validator("createdAt", mode="after")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return cast(datetime, _normalize_utc_datetime(value))


class OrderLineCandidate(AssociateModel):
    orderLineId: str
    productId: str
    sku: str | None = None
    productDescription: str | None = None
    productType: str | None = None
    shippedQuantity: int | float | None = None


class OrderCandidate(AssociateModel):
    customerReference: str
    customerName: str | None = None
    orderReference: str
    sourceWebOrderNumber: str | None = None
    trilogieOrderNumber: str | None = None
    orderSource: OrderSource = OrderSource.UNKNOWN
    orderStatus: str | None = None
    sellWarehouseId: str | None = None
    shipFromWarehouseId: str | None = None
    shippingMethod: str | None = None
    billingCity: str | None = None
    postalCode: str | None = None
    accountType: str | None = None
    retrievalScore: float | None = Field(default=None, ge=0.0)
    confidenceMillionths: int = Field(ge=0, le=1_000_000)
    evidenceSource: str
    lines: list[OrderLineCandidate]


class ClarificationOption(AssociateModel):
    value: str
    label: str
    candidateCount: int = Field(ge=1)


class ClarificationPrompt(AssociateModel):
    slot: str
    question: str
    options: list[ClarificationOption] = Field(min_length=2, max_length=12)


class DiscoveryLock(AssociateModel):
    customerReference: str
    orderReference: str
    sourceWebOrderNumber: str | None = None
    trilogieOrderNumber: str | None = None
    orderSource: OrderSource = OrderSource.UNKNOWN
    orderLineId: str
    productId: str
    lockDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmedBy: str
    confirmedAt: datetime

    @field_validator("confirmedAt", mode="after")
    @classmethod
    def normalize_confirmed_at(cls, value: datetime) -> datetime:
        return cast(datetime, _normalize_utc_datetime(value))


class AssociateConversationView(AssociateModel):
    id: str
    status: str
    anchorType: AnchorType
    anchorValueMasked: str
    orderSource: OrderSource = OrderSource.UNKNOWN
    discoveryAssessment: dict[str, Any] | None = None
    messages: list[ConversationMessage]
    candidates: list[OrderCandidate]
    discoveryLock: DiscoveryLock | None = None
    returnDetails: dict[str, Any] | None = None
    returnSessionId: str | None = None
    discoverySnapshotId: str | None = None
    confirmationSnapshotId: str | None = None
    activeDialogueState: str = "ENTITY_IDENTIFICATION"
    activeRequestedSlots: list[str] = Field(default_factory=list)
    clarificationPrompt: ClarificationPrompt | None = None
    candidateSetId: str | None = None
    candidateSetExpiresAt: datetime | None = None
    configurationReleaseId: str | None = None
    configurationChecksum: str | None = None
    configurationSource: str = "VERSION_CONTROLLED_BASELINE"
    lastMessageSequence: int = Field(default=0, ge=0)
    nextQuestion: str | None = None
    version: int = Field(ge=0)
    createdAt: datetime
    updatedAt: datetime

    @field_validator(
        "candidateSetExpiresAt",
        "createdAt",
        "updatedAt",
        mode="after",
    )
    @classmethod
    def normalize_timestamps(cls, value: datetime | None) -> datetime | None:
        return _normalize_utc_datetime(value)


class StartAssociateConversationRequest(AssociateModel):
    anchorType: AnchorType
    anchorValue: str = Field(min_length=2, max_length=256)

    @field_validator("anchorValue")
    @classmethod
    def normalize_anchor(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("anchorValue must not be blank")
        return normalized


class ContinueAssociateConversationRequest(StartAssociateConversationRequest):
    expectedVersion: int = Field(ge=0)


class AssociateChatTurnRequest(AssociateModel):
    message: str = Field(min_length=2, max_length=2_000)
    expectedVersion: int | None = Field(default=None, ge=0)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("message must not be blank")
        return normalized


class ConfirmDiscoveryRequest(AssociateModel):
    candidateIndex: int = Field(ge=0, le=99)
    orderLineId: str = Field(min_length=1, max_length=128)
    expectedVersion: int = Field(ge=0)
    candidateSetId: str | None = Field(default=None, min_length=1, max_length=128)


class ReturnDetailsRequest(AssociateModel):
    reasonCode: str = Field(min_length=1, max_length=64)
    returnQuantity: int = Field(ge=1, le=10_000)
    packageCount: int = Field(ge=1, le=10_000)
    shippingPathExpectation: NormalizedReturnMethod
    productPresence: ProductPresence = ProductPresence.PRESENT_AT_BRANCH
    branchReference: str | None = Field(default=None, max_length=128)
    associateReference: str | None = Field(default=None, max_length=128)
    pickupAssessment: dict[str, Any] | None = None
    attachmentIds: list[str] = Field(default_factory=list, max_length=100)
    notes: str | None = Field(default=None, max_length=2_000)
    expectedVersion: int = Field(ge=0)


def _now() -> datetime:
    return datetime.now(UTC)


def _is_expired(value: datetime | None) -> bool:
    normalized = _normalize_utc_datetime(value)
    if normalized is None:
        return False
    return normalized <= _now()


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _mask(value: str, anchor_type: AnchorType) -> str:
    if anchor_type not in {AnchorType.PHONE, AnchorType.EMAIL}:
        return value
    if len(value) <= 4:
        return "****"
    return f"{value[:2]}***{value[-2:]}"


def _nested(document: dict[str, Any], path: str) -> Any:
    value: Any = document
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _first_nested(document: dict[str, Any], paths: tuple[str, ...]) -> Any:
    for path in paths:
        value = _nested(document, path)
        if value not in (None, ""):
            return value
    return None


def _first_line_value(line: dict[str, Any], paths: tuple[str, ...]) -> Any:
    for path in paths:
        value = _nested(line, path)
        if value not in (None, ""):
            return value
    return None


class AssociateConversationService:
    """Graph-first discovery with targeted source fallback and immutable confirmation locks."""

    def __init__(
        self,
        *,
        platform_client: AsyncMongoClient[dict[str, object]],
        source_client: AsyncMongoClient[dict[str, object]],
        graph: AsyncDriver,
        settings: Settings,
        repository: OperationalRepository,
        return_configuration: ReturnPlatformConfiguration | None = None,
        configuration_release_id: str | None = None,
        configuration_checksum: str | None = None,
        configuration_source: str = "VERSION_CONTROLLED_BASELINE",
    ) -> None:
        self._db = platform_client[settings.mongo_database]
        self._source = source_client[settings.source_mongo_database]
        self._graph = graph
        self._graph_database = settings.neo4j_database
        self._settings = settings
        self._conversations = self._db["associate_conversations"]
        self._messages = self._db["associate_messages"]
        self._discovery_snapshots = self._db["discovery_snapshots"]
        self._return_request_snapshots = self._db["return_request_snapshots"]
        self._locks = self._db["discovery_locks"]
        self._repository = repository
        self._ai = AIGatewayService(cast(AIGatewayRepository, repository), settings)
        self._return_configuration = (
            return_configuration
            or load_return_configuration(settings.return_configuration_path).configuration
        )
        self._source_config = self._return_configuration.source_resolution
        self._configuration_release_id = configuration_release_id
        self._configuration_checksum = configuration_checksum or _digest(
            self._return_configuration.model_dump(mode="json")
        )
        self._configuration_source = configuration_source
        progressive = self._return_configuration.discovery.progressive
        self._progressive_conversation = ProgressiveConversationEngine[OrderCandidate](
            rules=tuple(
                DisambiguationRule(
                    slot=item.slot,
                    candidate_field=item.candidate_field,
                    question=item.question,
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
        self._order_discovery_agent = OrderDiscoveryAgent(self._return_configuration)
        self._return_workflow_agent = ReturnWorkflowAgent(self._return_configuration)
        self._return_support = ReturnSupportService(
            client=platform_client,
            settings=settings,
            configuration=self._return_configuration,
            operational_repository=repository,
        )

    def _is_greeting(self, value: str) -> bool:
        for pattern in self._return_configuration.discovery.conversation.greeting_patterns:
            if re.search(pattern, value.strip(), re.IGNORECASE) is not None:
                return True
        return False

    def _format_anchor_type(self, anchor_type: AnchorType) -> str:
        return anchor_type.value.lower().replace("_", " ")

    def _extract_anchor(self, message: str) -> StartAssociateConversationRequest:
        matches: list[tuple[AnchorType, str]] = []
        for extractor in self._return_configuration.discovery.anchor_extractors:
            if extractor.anchor_type not in AnchorType._value2member_map_:
                continue
            anchor_type = AnchorType(extractor.anchor_type)
            for pattern in extractor.patterns:
                found = re.search(pattern, message, re.IGNORECASE)
                if found is not None:
                    matches.append((anchor_type, found.group(0)))
                    break
        strong = tuple(
            AnchorType(value)
            for value in self._return_configuration.discovery.strong_anchors
            if value in AnchorType._value2member_map_
        )
        priority = {
            anchor_type: index
            for index, anchor_type in enumerate(
                (
                    *strong,
                    AnchorType.PHONE,
                    AnchorType.EMAIL,
                    AnchorType.SKU,
                )
            )
        }
        if matches:
            anchor_type, value = min(
                matches,
                key=lambda item: priority.get(item[0], len(priority)),
            )
            if anchor_type in {
                AnchorType.ORDER_NUMBER,
                AnchorType.SKU,
                AnchorType.TRACKING_NUMBER,
                AnchorType.CUSTOMER_ID,
            }:
                value = value.upper()
            return StartAssociateConversationRequest(
                anchorType=anchor_type,
                anchorValue=value,
            )
        fallback = AnchorType(self._return_configuration.discovery.free_text_fallback_anchor)
        return StartAssociateConversationRequest(
            anchorType=fallback,
            anchorValue=message,
        )

    def _deterministic_intent_fallback(
        self,
        message: str,
    ) -> StartAssociateConversationRequest:
        """Extract only bounded fragments; never synthesize missing identifier characters."""
        configured = self._extract_anchor(message)
        if configured.anchorType in _PROTECTED_ANCHOR_TYPES:
            return configured
        contextual_patterns: tuple[tuple[AnchorType, str], ...] = (
            (
                AnchorType.ORDER_NUMBER,
                r"\b(?:orders?|oders?|odr)\s*(?:number|no|id)?\s*"
                r"((?:SO|ORD)-[A-Z0-9-]+)\b",
            ),
            (
                AnchorType.CUSTOMER_ID,
                r"\b(?:customer|cust)\s*(?:number|no|id)?\s*((?:CUST|PTY)-[A-Z0-9-]+)\b",
            ),
            (
                AnchorType.TRACKING_NUMBER,
                r"\b(?:shipment\s+)?(?:tracking|track)\s*(?:number|no|id)?\s*"
                r"([A-Z0-9][A-Z0-9-]+)\b",
            ),
            (
                AnchorType.SKU,
                r"\bsku\s*(?:number|no|id)?\s*([A-Z0-9][A-Z0-9-]+)\b",
            ),
        )
        for anchor_type, pattern in contextual_patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match is not None:
                return StartAssociateConversationRequest(
                    anchorType=anchor_type,
                    anchorValue=match.group(1).upper(),
                )
        product_match = re.search(
            r"\b(?:containing|contains|product)\s+(.+?)\s*[?.!]*$",
            message,
            re.IGNORECASE,
        )
        if product_match is not None:
            return StartAssociateConversationRequest(
                anchorType=AnchorType.PRODUCT_DESCRIPTION,
                anchorValue=product_match.group(1).strip(),
            )
        customer_match = re.search(
            r"\b(?:from|for|named?)\s+([A-Z][A-Z'-]*(?:\s+[A-Z][A-Z'-]*){0,2})\s*[?.!]*$",
            message,
            re.IGNORECASE,
        )
        if customer_match is not None:
            return StartAssociateConversationRequest(
                anchorType=AnchorType.CUSTOMER_NAME,
                anchorValue=customer_match.group(1).strip(),
            )
        return configured

    def _validated_ai_anchor(
        self,
        message: str,
        *,
        explanation: str | None,
        confidence_millionths: int | None,
    ) -> StartAssociateConversationRequest | None:
        if (
            explanation is None
            or confidence_millionths is None
            or confidence_millionths < _DISCOVERY_INTENT_CONFIDENCE_THRESHOLD
        ):
            return None
        try:
            payload = json.loads(explanation)
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict) or set(payload) != {"anchorType", "anchorValue"}:
            return None
        raw_type = payload.get("anchorType")
        raw_value = payload.get("anchorValue")
        if not isinstance(raw_type, str) or raw_type not in AnchorType._value2member_map_:
            return None
        if not isinstance(raw_value, str):
            return None
        try:
            anchor = StartAssociateConversationRequest(
                anchorType=AnchorType(raw_type),
                anchorValue=raw_value,
            )
        except ValueError:
            return None
        configured = self._extract_anchor(message)
        if configured.anchorType in _PROTECTED_ANCHOR_TYPES:
            return configured
        if (
            anchor.anchorType in _PROTECTED_ANCHOR_TYPES
            and anchor.anchorValue.casefold() not in message.casefold()
        ):
            return None
        return anchor

    async def _resolve_discovery_intent(
        self,
        message: str,
    ) -> StartAssociateConversationRequest:
        configured = self._extract_anchor(message)
        if configured.anchorType in _PROTECTED_ANCHOR_TYPES:
            return configured
        try:
            evaluation = await self._ai.evaluate(
                session_id=None,
                redacted_input={"utterance": message},
                task_id="RETURN_DISCOVERY_INTENT_V1",
            )
            trace = evaluation.trace
            if (
                not evaluation.pending_interception
                and not trace.fallbackUsed
                and trace.decision is AIDecision.REVIEW_REQUIRED
            ):
                validated = self._validated_ai_anchor(
                    message,
                    explanation=trace.explanation,
                    confidence_millionths=trace.confidenceMillionths,
                )
                if validated is not None:
                    return validated
        except Exception:
            logger.warning("Discovery intent gateway failed; using deterministic fallback.")
        return self._deterministic_intent_fallback(message)

    def _is_strong_anchor(self, anchor_type: AnchorType) -> bool:
        return anchor_type.value in set(self._return_configuration.discovery.strong_anchors)

    @staticmethod
    def _fuzzy_tokens(value: str) -> tuple[str, ...]:
        return tuple(
            token
            for token in re.findall(r"[A-Za-z0-9]+", value)[:32]
            if token.lower() not in _FUZZY_STOP_WORDS and len(token) >= 3
        )[:8]

    def _fuzzy_query(self, value: str, *, require_all: bool = True) -> str:
        """Build a bounded Lucene fuzzy query without exposing query syntax injection."""

        config = self._return_configuration.discovery.progressive
        fuzzy_tokens: list[str] = []
        for token in self._fuzzy_tokens(value):
            if len(token) >= config.two_edit_min_token_length:
                edits = min(config.max_edit_distance, 2)
            elif len(token) >= config.one_edit_min_token_length:
                edits = min(config.max_edit_distance, 1)
            else:
                edits = 0
            fuzzy_tokens.append(f"{token}~{edits}" if edits else token)
        return (" AND " if require_all else " OR ").join(fuzzy_tokens)

    def _customer_name_query(self, value: str) -> str:
        """Combine safe prefix matching with configured fuzzy variants."""
        config = self._return_configuration.discovery.progressive
        clauses: list[str] = []
        tokens = tuple(
            token
            for token in re.findall(r"[A-Za-z0-9]+", value)[:8]
            if token.lower() not in _FUZZY_STOP_WORDS and len(token) >= 2
        )
        for token in tokens:
            variants = [f"{token}*"]
            if len(token) >= config.two_edit_min_token_length:
                variants.append(f"{token}~{min(config.max_edit_distance, 2)}")
            elif len(token) >= config.one_edit_min_token_length:
                variants.append(f"{token}~{min(config.max_edit_distance, 1)}")
            clauses.append(variants[0] if len(variants) == 1 else f"({' OR '.join(variants)})")
        return " AND ".join(clauses)

    @staticmethod
    def _candidate_value(candidate: OrderCandidate, field: str) -> str | None:
        if field == "productDescription":
            descriptions = tuple(
                dict.fromkeys(
                    value
                    for line in candidate.lines
                    if (value := " ".join((line.productDescription or "").split()).strip())
                )
            )
            return " / ".join(descriptions) or None
        if field == "sku":
            skus = tuple(
                dict.fromkeys(
                    value
                    for line in candidate.lines
                    if (value := " ".join((line.sku or "").split()).strip())
                )
            )
            return " / ".join(skus) or None
        candidate_value = getattr(candidate, field, None)
        if candidate_value is None:
            return None
        normalized = " ".join(str(candidate_value).split()).strip()
        return normalized or None

    def _select_disambiguation_attribute(
        self,
        candidates: list[OrderCandidate],
        *,
        excluded_slots: set[str] | None = None,
    ) -> tuple[str, str] | None:
        selected = self._progressive_conversation.select_rule(
            candidates,
            value_for=self._candidate_value,
            excluded_slots=excluded_slots,
        )
        return (selected.slot, selected.question) if selected is not None else None

    def _clarification_prompt(
        self,
        candidates: list[OrderCandidate],
        requested_slots: list[str],
        question: str | None,
    ) -> ClarificationPrompt | None:
        if not requested_slots or not question:
            return None
        slot = requested_slots[0]
        attribute = next(
            (
                item
                for item in (
                    self._return_configuration.discovery.progressive.disambiguation_attributes
                )
                if item.slot == slot
            ),
            None,
        )
        if attribute is None:
            return None
        counts: dict[str, int] = {}
        labels: dict[str, str] = {}
        for candidate in candidates:
            value = self._candidate_value(candidate, attribute.candidate_field)
            if value is None:
                continue
            normalized = value.casefold()
            counts[normalized] = counts.get(normalized, 0) + 1
            labels.setdefault(normalized, value)
        options = [
            ClarificationOption(
                value=labels[normalized],
                label=labels[normalized],
                candidateCount=count,
            )
            for normalized, count in sorted(
                counts.items(),
                key=lambda item: (labels[item[0]].casefold(), item[0]),
            )
        ]
        if len(options) < 2:
            return None
        return ClarificationPrompt(slot=slot, question=question, options=options)

    def _clarification_prompt_payload(
        self,
        candidates: list[OrderCandidate],
        requested_slots: list[str],
        question: str | None,
    ) -> dict[str, Any] | None:
        prompt = self._clarification_prompt(candidates, requested_slots, question)
        return None if prompt is None else prompt.model_dump(mode="json")

    def _dialogue_projection(
        self,
        candidates: list[OrderCandidate],
    ) -> tuple[str, list[str], str | None, datetime | None, str | None]:
        """Return state, requested slots, candidate-set identity, expiry, and question."""

        decision = self._progressive_conversation.project(
            candidates,
            value_for=self._candidate_value,
            default_ambiguity_question=(
                self._return_configuration.discovery.conversation.default_discovery_question
            ),
        )
        return (
            decision.state,
            list(decision.requested_slots),
            decision.candidate_set_id,
            decision.candidate_set_expires_at,
            decision.question,
        )

    @staticmethod
    def _slot_response_matches(candidate_value: str | None, response: str) -> bool:
        return ProgressiveConversationEngine.response_matches(candidate_value, response)

    async def _generate_smart_question(
        self,
        conversation: AssociateConversationView,
    ) -> str:
        if conversation.status == self._return_configuration.discovery.conversation.greeting_status:
            return self._return_configuration.discovery.conversation.greeting_next_question

        configured_question: str | None = None
        task_id = "RETURN_SMART_QUESTION_V1"
        strong = list(self._return_configuration.discovery.strong_anchors)
        known = [
            f"recognized evidence category: {conversation.anchorType.value}",
            f"source-backed candidate count: {len(conversation.candidates)}",
            f"discovery status: {conversation.status}",
            f"confirmation lock present: {conversation.discoveryLock is not None}",
        ]

        if conversation.activeRequestedSlots:
            requested = conversation.activeRequestedSlots[0]
            attributes = self._return_configuration.discovery.progressive.disambiguation_attributes
            attribute = next((item for item in attributes if item.slot == requested), None)
            if attribute is not None:
                configured_question = attribute.question
                approved_values = sorted(
                    {
                        value
                        for candidate in conversation.candidates
                        if (
                            value := self._candidate_value(
                                candidate,
                                attribute.candidate_field,
                            )
                        )
                    }
                )[:20]
                known.extend(
                    (
                        f"deterministically selected attribute: {requested.replace('_', ' ')}",
                        "approved candidate values: "
                        + (", ".join(approved_values) if approved_values else "not available"),
                        f"deterministic fallback wording: {configured_question}",
                    )
                )
            missing = [requested.replace("_", " ").lower()]
            task_id = "RETURN_PROGRESSIVE_DISAMBIGUATION_V1"
        else:
            missing = (
                [
                    item.field.replace("_", " ").lower()
                    for item in sorted(
                        self._return_configuration.smart_questions.fields,
                        key=lambda item: -item.priority,
                    )
                    if item.customer_answerable
                ]
                if conversation.discoveryLock is not None
                else [
                    value.replace("_", " ").lower()
                    for value in strong
                    if value != conversation.anchorType.value
                ]
            )

        try:
            evaluation = await self._ai.evaluate(
                session_id=conversation.id,
                redacted_input={
                    "missingFields": missing,
                    "knownFacts": known,
                    "returnPath": "associate conversational discovery",
                },
                task_id=task_id,
            )
            question = (evaluation.trace.explanation or "").strip()
            if question and question.endswith("?") and len(question) <= 500:
                return question
        except Exception as exc:
            logger.warning(
                "smart_question_ai_unavailable_using_deterministic_fallback",
                extra={
                    "conversation_id": conversation.id,
                    "error_type": type(exc).__name__,
                    "task_id": task_id,
                },
            )

        if configured_question is not None:
            return configured_question
        subject = (
            "the matching order and item"
            if conversation.candidates
            else (missing[0] if missing else "one more order detail")
        )
        return self._return_configuration.discovery.conversation.fallback_question_template.format(
            subject=subject
        )

    async def _apply_chat_copy(
        self,
        conversation: AssociateConversationView,
        *,
        raw_message: str,
    ) -> AssociateConversationView:
        question = await self._generate_smart_question(conversation)
        message_index = max(0, len(conversation.messages) - 2)
        assistant_index = max(0, len(conversation.messages) - 1)
        sequence = max(1, conversation.lastMessageSequence - 1)
        assistant_sequence = max(1, conversation.lastMessageSequence)
        assistant_text = conversation.messages[-1].content
        if conversation.status == self._return_configuration.discovery.conversation.greeting_status:
            planned_response = assistant_text
        else:
            planned_response = f"{assistant_text} {question}"
        clarification_prompt = (
            conversation.clarificationPrompt.model_copy(update={"question": question}).model_dump(
                mode="json"
            )
            if conversation.clarificationPrompt is not None
            else None
        )
        updated = await self._conversations.find_one_and_update(
            {"_id": conversation.id, "version": conversation.version},
            {
                "$set": {
                    f"messages.{message_index}.content": raw_message,
                    f"messages.{assistant_index}.content": planned_response,
                    "nextQuestion": question,
                    "clarificationPrompt": clarification_prompt,
                    "updatedAt": _now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        await self._messages.update_one(
            {"conversationId": conversation.id, "sequence": sequence},
            {"$set": {"messageText": raw_message}},
        )
        await self._messages.update_one(
            {"conversationId": conversation.id, "sequence": assistant_sequence},
            {"$set": {"messageText": planned_response}},
        )
        return conversation if updated is None else self._view(cast(dict[str, Any], updated))

    async def ensure_indexes(self) -> None:
        await self._conversations.create_index([("createdAt", -1)])
        await self._conversations.create_index("status")
        await self._messages.create_index([("conversationId", 1), ("sequence", 1)], unique=True)
        await self._messages.create_index([("conversationId", 1), ("createdAt", 1)])
        await self._discovery_snapshots.create_index("snapshotId", unique=True)
        await self._discovery_snapshots.create_index(
            [("conversationId", 1), ("snapshotType", 1), ("contentDigest", 1)],
            unique=True,
        )
        await self._return_request_snapshots.create_index("requestSnapshotId", unique=True)
        await self._return_request_snapshots.create_index(
            [("sessionId", 1), ("contentDigest", 1)], unique=True
        )
        index_info = await self._locks.index_information()
        for obsolete_name in ("lockDigest_1", "orderReference_1_orderLineId_1"):
            if obsolete_name in index_info:
                await self._locks.drop_index(obsolete_name)
        await self._locks.create_index("lockDigest")
        await self._locks.create_index("expiresAt", expireAfterSeconds=0)
        await self._locks.create_index(
            [("lockKey", 1)],
            unique=True,
            partialFilterExpression={"status": "ACTIVE"},
            name="active_discovery_lock_unique",
        )

    @staticmethod
    def _view(document: dict[str, Any]) -> AssociateConversationView:
        return AssociateConversationView.model_validate(
            {
                "id": str(document["_id"]),
                **{
                    key: value
                    for key, value in document.items()
                    if key not in ("_id", "anchorDigest", "createdBy")
                },
            }
        )

    @staticmethod
    def _message(role: str, content: str) -> dict[str, Any]:
        return {
            "id": str(uuid.uuid4()),
            "role": role,
            "content": content,
            "createdAt": _now(),
        }

    async def _persist_messages(
        self,
        conversation_id: str,
        *,
        starting_sequence: int,
        messages: list[dict[str, Any]],
    ) -> None:
        for offset, message in enumerate(messages):
            sequence = starting_sequence + offset
            document = {
                "_id": message["id"],
                "conversationId": conversation_id,
                "sequence": sequence,
                "speakerRole": message["role"],
                "messageText": message["content"],
                "attachmentIds": [],
                "extractedEvidence": [],
                "createdAt": message["createdAt"],
            }
            await self._messages.update_one(
                {"conversationId": conversation_id, "sequence": sequence},
                {"$setOnInsert": document},
                upsert=True,
            )

    async def _persist_discovery_snapshot(
        self,
        *,
        conversation_id: str,
        snapshot_type: str,
        payload: dict[str, Any],
        actor_id: str,
    ) -> str:
        content_digest = _digest(payload)
        snapshot_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{conversation_id}:{snapshot_type}:{content_digest}",
            )
        )
        await self._discovery_snapshots.update_one(
            {
                "conversationId": conversation_id,
                "snapshotType": snapshot_type,
                "contentDigest": content_digest,
            },
            {
                "$setOnInsert": {
                    "_id": snapshot_id,
                    "snapshotId": snapshot_id,
                    "conversationId": conversation_id,
                    "snapshotType": snapshot_type,
                    "payload": payload,
                    "contentDigest": content_digest,
                    "schemaVersion": "1.0",
                    "createdBy": actor_id,
                    "createdAt": _now(),
                }
            },
            upsert=True,
        )
        return snapshot_id

    async def _persist_return_request_snapshot(
        self,
        *,
        session_id: str,
        conversation_id: str,
        payload: dict[str, Any],
        actor_id: str,
    ) -> tuple[str, str]:
        content_digest = _digest(payload)
        snapshot_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{session_id}:{content_digest}"))
        await self._return_request_snapshots.update_one(
            {"sessionId": session_id, "contentDigest": content_digest},
            {
                "$setOnInsert": {
                    "_id": snapshot_id,
                    "requestSnapshotId": snapshot_id,
                    "sessionId": session_id,
                    "conversationId": conversation_id,
                    "payload": payload,
                    "contentDigest": content_digest,
                    "schemaVersion": "1.0",
                    "confirmedBy": actor_id,
                    "confirmedAt": _now(),
                    "submittedAt": _now(),
                }
            },
            upsert=True,
        )
        return snapshot_id, content_digest

    async def _graph_candidates(
        self, anchor_type: AnchorType, anchor_value: str
    ) -> list[OrderCandidate]:
        normalized_hash = (
            contact_lookup_digest(
                anchor_value,
                "PHONE" if anchor_type is AnchorType.PHONE else "EMAIL",
                self._settings.contact_lookup_hmac_key.get_secret_value(),
            )
            if anchor_type in {AnchorType.PHONE, AnchorType.EMAIL}
            else ""
        )
        progressive = self._return_configuration.discovery.progressive
        if progressive.enabled and anchor_type is AnchorType.CUSTOMER_NAME:
            fuzzy_query = self._customer_name_query(anchor_value)
            if not fuzzy_query:
                return []
            records, _, _ = await self._graph.execute_query(
                """
                CALL db.index.fulltext.queryNodes($indexName, $query, {limit: $limit})
                YIELD node AS c, score
                MATCH (c)-[:PLACED_ORDER]->(o:SalesOrder)
                OPTIONAL MATCH (o)-[:HAS_ORDER_LINE]->(l:OrderLine)
                OPTIONAL MATCH (l)-[:REFERENCES_PRODUCT]->(p:Product)
                RETURN c,o,score,collect({line:l,product:p}) AS lines
                ORDER BY score DESC
                LIMIT $limit
                """,
                indexName=progressive.customer_fulltext_index,
                query=fuzzy_query,
                limit=progressive.candidate_limit,
                database_=self._graph_database,
            )
        elif progressive.enabled and anchor_type is AnchorType.PRODUCT_DESCRIPTION:
            fuzzy_query = self._fuzzy_query(anchor_value, require_all=False)
            if not fuzzy_query:
                return []
            product_records, _, _ = await self._graph.execute_query(
                """
                CALL db.index.fulltext.queryNodes($indexName, $query, {limit: $limit})
                YIELD node AS p, score
                MATCH (o:SalesOrder)-[:HAS_ORDER_LINE]->(l:OrderLine)
                MATCH (l)-[:REFERENCES_PRODUCT]->(p)
                MATCH (c:Customer)-[:PLACED_ORDER]->(o)
                OPTIONAL MATCH (o)-[:HAS_ORDER_LINE]->(allLine:OrderLine)
                OPTIONAL MATCH (allLine)-[:REFERENCES_PRODUCT]->(allProduct:Product)
                RETURN c,o,score,collect({line:allLine,product:allProduct}) AS lines
                ORDER BY score DESC
                LIMIT $limit
                """,
                indexName=progressive.product_fulltext_index,
                query=fuzzy_query,
                limit=progressive.candidate_limit,
                database_=self._graph_database,
            )
            customer_records, _, _ = await self._graph.execute_query(
                """
                CALL db.index.fulltext.queryNodes($indexName, $query, {limit: $limit})
                YIELD node AS c, score
                MATCH (c)-[:PLACED_ORDER]->(o:SalesOrder)
                OPTIONAL MATCH (o)-[:HAS_ORDER_LINE]->(l:OrderLine)
                OPTIONAL MATCH (l)-[:REFERENCES_PRODUCT]->(p:Product)
                RETURN c,o,score,collect({line:l,product:p}) AS lines
                ORDER BY score DESC
                LIMIT $limit
                """,
                indexName=progressive.customer_fulltext_index,
                query=fuzzy_query,
                limit=progressive.candidate_limit,
                database_=self._graph_database,
            )
            records = [*product_records, *customer_records]
        else:
            records = []
        queries = {
            AnchorType.ORDER_NUMBER: (
                "MATCH (c:Customer)-[:PLACED_ORDER]->(o:SalesOrder) "
                "WHERE toLower(o.sales_order_number) STARTS WITH toLower($value) "
                "OPTIONAL MATCH (o)-[:HAS_ORDER_LINE]->(l:OrderLine) "
                "OPTIONAL MATCH (l)-[:REFERENCES_PRODUCT]->(p:Product) "
                "RETURN c,o,collect({line:l,product:p}) AS lines, "
                "CASE WHEN toLower(o.sales_order_number)=toLower($value) "
                "THEN 1.0 ELSE 0.5 END AS score "
                "ORDER BY score DESC LIMIT 20",
                anchor_value,
            ),
            AnchorType.CUSTOMER_ID: (
                "MATCH (c:Customer)-[:PLACED_ORDER]->(o:SalesOrder) "
                "WHERE toLower(c.customer_id) STARTS WITH toLower($value) "
                "OR toLower(c.customer_key) STARTS WITH toLower($value) "
                "OPTIONAL MATCH (o)-[:HAS_ORDER_LINE]->(l:OrderLine) "
                "OPTIONAL MATCH (l)-[:REFERENCES_PRODUCT]->(p:Product) "
                "RETURN c,o,collect({line:l,product:p}) AS lines, "
                "CASE WHEN toLower(c.customer_id)=toLower($value) "
                "OR toLower(c.customer_key)=toLower($value) THEN 1.0 ELSE 0.5 END AS score "
                "ORDER BY score DESC LIMIT 20",
                anchor_value,
            ),
            AnchorType.PHONE: (
                "MATCH (c:Customer {phone_hash:$value})-[:PLACED_ORDER]->(o:SalesOrder) "
                "OPTIONAL MATCH (o)-[:HAS_ORDER_LINE]->(l:OrderLine) "
                "OPTIONAL MATCH (l)-[:REFERENCES_PRODUCT]->(p:Product) "
                "RETURN c,o,collect({line:l,product:p}) AS lines LIMIT 20",
                normalized_hash,
            ),
            AnchorType.EMAIL: (
                "MATCH (c:Customer {email_hash:$value})-[:PLACED_ORDER]->(o:SalesOrder) "
                "OPTIONAL MATCH (o)-[:HAS_ORDER_LINE]->(l:OrderLine) "
                "OPTIONAL MATCH (l)-[:REFERENCES_PRODUCT]->(p:Product) "
                "RETURN c,o,collect({line:l,product:p}) AS lines LIMIT 20",
                normalized_hash,
            ),
            AnchorType.TRACKING_NUMBER: (
                "MATCH (o:SalesOrder)-[:HAS_ORIGINAL_SHIPMENT]->(s:Shipment) "
                "WHERE toLower(s.tracking_number) STARTS WITH toLower($value) "
                "MATCH (c:Customer)-[:PLACED_ORDER]->(o) "
                "OPTIONAL MATCH (o)-[:HAS_ORDER_LINE]->(l:OrderLine) "
                "OPTIONAL MATCH (l)-[:REFERENCES_PRODUCT]->(p:Product) "
                "RETURN c,o,collect({line:l,product:p}) AS lines, "
                "CASE WHEN toLower(s.tracking_number)=toLower($value) "
                "THEN 1.0 ELSE 0.5 END AS score "
                "ORDER BY score DESC LIMIT 20",
                anchor_value,
            ),
            AnchorType.SKU: (
                "MATCH (o:SalesOrder)-[:HAS_ORDER_LINE]->(l:OrderLine) "
                "MATCH (l)-[:REFERENCES_PRODUCT]->(p:Product) "
                "WHERE toLower(p.sku) STARTS WITH toLower($value) "
                "OR toLower(p.product_id) STARTS WITH toLower($value) "
                "MATCH (c:Customer)-[:PLACED_ORDER]->(o) "
                "RETURN c,o,collect({line:l,product:p}) AS lines, "
                "CASE WHEN toLower(p.sku)=toLower($value) "
                "OR toLower(p.product_id)=toLower($value) THEN 1.0 ELSE 0.5 END AS score "
                "ORDER BY score DESC LIMIT 20",
                anchor_value,
            ),
        }
        if not records:
            query_definition = queries.get(anchor_type)
            if query_definition is None:
                return []
            query, query_value = query_definition
            records, _, _ = await self._graph.execute_query(
                query, value=query_value, database_=self._graph_database
            )
        candidates_by_key: dict[tuple[str, str], OrderCandidate] = {}
        for record in records:
            customer = dict(record["c"])
            order = dict(record["o"])
            raw_retrieval_score = record.get("score")
            retrieval_score = (
                float(raw_retrieval_score) if raw_retrieval_score is not None else None
            )
            lines: list[OrderLineCandidate] = []
            for entry in record["lines"]:
                line = dict(entry["line"]) if entry.get("line") is not None else {}
                product = dict(entry["product"]) if entry.get("product") is not None else {}
                if not line:
                    continue
                lines.append(
                    OrderLineCandidate(
                        orderLineId=str(line.get("order_line_key", "")),
                        productId=str(product.get("product_id", line.get("product_id", ""))),
                        sku=cast(str | None, product.get("sku")),
                        productDescription=cast(str | None, product.get("product_description")),
                        productType=cast(str | None, product.get("product_type")),
                        shippedQuantity=cast(int | float | None, line.get("shipped_quantity")),
                    )
                )
            if lines:
                candidate = OrderCandidate(
                    customerReference=str(
                        customer.get("customer_id") or customer.get("customer_key")
                    ),
                    customerName=cast(str | None, customer.get("customer_name")),
                    orderReference=str(order.get("sales_order_number")),
                    sourceWebOrderNumber=cast(str | None, order.get("source_web_order_number")),
                    trilogieOrderNumber=cast(
                        str | None,
                        order.get("trilogie_order_number") or order.get("sales_order_number"),
                    ),
                    orderSource=(
                        OrderSource.FERGUSONHOME_WEB
                        if order.get("source_web_order_number")
                        else OrderSource.UNKNOWN
                    ),
                    orderStatus=cast(str | None, order.get("order_status")),
                    sellWarehouseId=cast(str | None, order.get("sell_warehouse_id")),
                    shipFromWarehouseId=cast(str | None, order.get("ship_from_warehouse_id")),
                    shippingMethod=cast(str | None, order.get("shipping_method")),
                    billingCity=cast(
                        str | None,
                        customer.get("billing_city")
                        or customer.get("city")
                        or customer.get("normalized_city"),
                    ),
                    postalCode=cast(
                        str | None,
                        customer.get("postal_code") or customer.get("billing_postal_code"),
                    ),
                    accountType=cast(str | None, customer.get("account_type")),
                    retrievalScore=retrieval_score,
                    confidenceMillionths=(
                        self._return_configuration.discovery.anchor_weights.get(
                            anchor_type.value,
                            0,
                        )
                    ),
                    evidenceSource="NEO4J_GRAPH",
                    lines=lines,
                )
                key = (candidate.customerReference, candidate.orderReference)
                current = candidates_by_key.get(key)
                if current is None or (candidate.retrievalScore or 0) > (
                    current.retrievalScore or 0
                ):
                    candidates_by_key[key] = candidate
        return sorted(
            candidates_by_key.values(),
            key=lambda item: item.retrievalScore or 0,
            reverse=True,
        )[: progressive.candidate_limit]

    def _case_insensitive_query(self, value: str, *, exact: bool = True) -> dict[str, Any]:
        suffix = "$" if exact else ""
        return {"$regex": f"^{re.escape(value.strip())}{suffix}", "$options": "i"}

    def _direct_source_query(
        self,
        anchor_type: AnchorType,
        matcher: dict[str, Any],
    ) -> dict[str, Any]:
        config = self._source_config
        if anchor_type is AnchorType.ORDER_NUMBER:
            paths = dict.fromkeys((*config.order_number_paths, *config.web_order_paths))
            return {"$or": [{path: matcher} for path in paths]}
        if anchor_type is AnchorType.CUSTOMER_ID:
            return {"$or": [{path: matcher} for path in config.customer_id_paths]}
        if anchor_type is AnchorType.SKU:
            paths = dict.fromkeys((*config.sku_paths, *config.product_id_paths))
            return {"$or": [{f"salesLines.lineData.{path}": matcher} for path in paths]}
        raise ValueError("Unsupported direct source anchor.")

    async def _source_documents(
        self, anchor_type: AnchorType, anchor_value: str
    ) -> list[dict[str, Any]]:
        config = self._source_config
        sales = self._source[config.sales_invoice_collection]
        matcher = self._case_insensitive_query(anchor_value)
        if anchor_type in {
            AnchorType.ORDER_NUMBER,
            AnchorType.CUSTOMER_ID,
            AnchorType.SKU,
        }:
            exact_query = self._direct_source_query(anchor_type, matcher)
            exact_documents = await sales.find(exact_query).limit(20).to_list()
            if exact_documents:
                return [cast(dict[str, Any], item) for item in exact_documents]
            prefix_query = self._direct_source_query(
                anchor_type,
                self._case_insensitive_query(anchor_value, exact=False),
            )
            prefix_documents = await sales.find(prefix_query).limit(20).to_list()
            return [cast(dict[str, Any], item) for item in prefix_documents]
        if anchor_type in {AnchorType.PHONE, AnchorType.EMAIL}:
            field = config.phone_field if anchor_type is AnchorType.PHONE else config.email_field
            customer = await self._source[config.customer_collection].find_one({field: matcher})
            customer_id = (
                _nested(cast(dict[str, Any], customer), config.customer_master_id_field)
                if customer
                else None
            )
            if customer_id is None:
                return []
            cust_matcher = self._case_insensitive_query(str(customer_id))
            query = {"$or": [{path: cust_matcher} for path in config.customer_id_paths]}
        elif anchor_type is AnchorType.TRACKING_NUMBER:
            shipments = (
                await self._source[config.shipment_collection]
                .find({config.tracking_field: matcher})
                .limit(20)
                .to_list()
            )
            if not shipments:
                prefix_matcher = self._case_insensitive_query(anchor_value, exact=False)
                shipments = (
                    await self._source[config.shipment_collection]
                    .find({config.tracking_field: prefix_matcher})
                    .limit(20)
                    .to_list()
                )
            order_ids = [
                str(value)
                for shipment in shipments
                if (
                    value := _nested(
                        cast(dict[str, Any], shipment),
                        config.tracking_order_field,
                    )
                )
            ]
            if not order_ids:
                return []
            query = {"$or": [{path: {"$in": order_ids}} for path in config.order_number_paths]}
        elif anchor_type is AnchorType.CUSTOMER_NAME:
            tokens = tuple(
                token for token in re.findall(r"[A-Za-z0-9]+", anchor_value)[:8] if len(token) >= 2
            )
            if not tokens:
                return []
            name_prefix = r"\s+".join(re.escape(token) for token in tokens)
            query = {
                "$or": [
                    {
                        path: {
                            "$regex": f"^{name_prefix}",
                            "$options": "i",
                        }
                    }
                    for path in config.customer_name_paths
                ]
            }
        else:
            tokens = self._fuzzy_tokens(anchor_value) or (anchor_value.strip(),)
            query = {
                "$or": [
                    {field: {"$regex": re.escape(token), "$options": "i"}}
                    for field in (
                        *config.customer_name_paths,
                        *(
                            f"salesLines.lineData.{path}"
                            for path in config.product_description_paths
                        ),
                    )
                    for token in tokens
                ]
            }
        cursor = sales.find(query).limit(20)
        return [cast(dict[str, Any], item) async for item in cursor]

    def _source_candidate(self, document: dict[str, Any]) -> OrderCandidate | None:
        config = self._source_config
        source_web_order = _first_nested(document, config.web_order_paths)
        trilogie_order = _first_nested(document, config.trilogie_order_paths)
        order_reference = (
            trilogie_order or source_web_order or _first_nested(document, config.order_number_paths)
        )
        customer_reference = _first_nested(document, config.customer_id_paths)
        if order_reference is None or customer_reference is None:
            return None
        lines: list[OrderLineCandidate] = []
        raw_lines = document.get("salesLines", [])
        if isinstance(raw_lines, list):
            for position, wrapper in enumerate(raw_lines):
                if not isinstance(wrapper, dict):
                    continue
                line = wrapper.get("lineData", wrapper)
                if not isinstance(line, dict):
                    continue
                product_id = _first_line_value(line, config.product_id_paths)
                if product_id is None:
                    continue
                line_id = _first_line_value(line, config.line_id_paths)
                lines.append(
                    OrderLineCandidate(
                        orderLineId=str(line_id or f"{order_reference}:LINE:{position + 1}"),
                        productId=str(product_id),
                        sku=cast(str | None, _first_line_value(line, config.sku_paths)),
                        productDescription=cast(
                            str | None,
                            _first_line_value(line, config.product_description_paths),
                        ),
                        productType=cast(str | None, line.get("productType")),
                        shippedQuantity=cast(
                            int | float | None,
                            _first_line_value(line, config.shipped_quantity_paths),
                        ),
                    )
                )
        if not lines:
            return None
        return OrderCandidate(
            customerReference=str(customer_reference),
            customerName=cast(str | None, _first_nested(document, config.customer_name_paths)),
            orderReference=str(order_reference),
            sourceWebOrderNumber=(str(source_web_order) if source_web_order is not None else None),
            trilogieOrderNumber=(str(trilogie_order) if trilogie_order is not None else None),
            orderSource=(
                OrderSource.FERGUSONHOME_WEB
                if source_web_order is not None
                else OrderSource.UNKNOWN
            ),
            orderStatus=cast(str | None, _nested(document, "salesHdrEventData.orderStatus")),
            sellWarehouseId=cast(str | None, _nested(document, "salesHdrEventData.sellWhseId")),
            shipFromWarehouseId=cast(
                str | None, _nested(document, "salesHdrEventData.shipFromWhseId")
            ),
            shippingMethod=cast(str | None, _nested(document, "salesHdr.shipping.shipViaCode")),
            billingCity=cast(
                str | None,
                _first_nested(document, config.customer_city_paths),
            ),
            postalCode=cast(
                str | None,
                _first_nested(document, config.customer_postal_code_paths),
            ),
            accountType=cast(
                str | None,
                _first_nested(document, config.customer_account_type_paths),
            ),
            retrievalScore=None,
            confidenceMillionths=900_000,
            evidenceSource="SOURCE_MONGODB_TARGETED_FALLBACK",
            lines=lines,
        )

    async def _targeted_graph_upsert(self, candidates: list[OrderCandidate]) -> None:
        rows = [candidate.model_dump(mode="json") for candidate in candidates]
        if not rows:
            return
        await self._graph.execute_query(
            """
            UNWIND $rows AS row
            MERGE (c:Customer {customer_key: row.customerReference})
            SET c.customer_id=row.customerReference, c.customer_name=row.customerName,
                c.billing_city=row.billingCity, c.postal_code=row.postalCode,
                c.account_type=row.accountType,
                c.graph_synced_at=$syncedAt, c.sync_run_id=$syncRunId
            MERGE (o:SalesOrder {sales_order_number: row.orderReference})
            SET o.order_status=row.orderStatus, o.sell_warehouse_id=row.sellWarehouseId,
                o.ship_from_warehouse_id=row.shipFromWarehouseId,
                o.shipping_method=row.shippingMethod,
                o.source_web_order_number=row.sourceWebOrderNumber,
                o.trilogie_order_number=row.trilogieOrderNumber,
                o.graph_synced_at=$syncedAt, o.sync_run_id=$syncRunId
            MERGE (c)-[:PLACED_ORDER]->(o)
            FOREACH (_ IN CASE WHEN row.sourceWebOrderNumber IS NULL THEN [] ELSE [1] END |
                MERGE (w:WebOrder {web_order_number: row.sourceWebOrderNumber})
                SET w.graph_synced_at=$syncedAt, w.sync_run_id=$syncRunId
                MERGE (w)-[:RESOLVES_TO]->(o))
            FOREACH (line IN row.lines |
                MERGE (l:OrderLine {order_line_key: line.orderLineId})
                SET l.shipped_quantity=line.shippedQuantity, l.graph_synced_at=$syncedAt,
                    l.sync_run_id=$syncRunId
                MERGE (p:Product {product_id: line.productId})
                SET p.sku=line.sku, p.product_description=line.productDescription,
                    p.product_type=line.productType, p.graph_synced_at=$syncedAt,
                    p.sync_run_id=$syncRunId
                MERGE (o)-[:HAS_ORDER_LINE]->(l)
                MERGE (l)-[:REFERENCES_PRODUCT]->(p))
            """,
            rows=rows,
            syncedAt=_now().isoformat(),
            syncRunId=f"TARGETED:{uuid.uuid4()}",
            database_=self._graph_database,
        )

    async def _discover_candidates(
        self,
        anchor_type: AnchorType,
        anchor_value: str,
    ) -> list[OrderCandidate]:
        """Resolve candidates through graph-first routing with an approved source fallback."""

        graph_available = True
        try:
            candidates = await self._graph_candidates(anchor_type, anchor_value)
        except (Neo4jError, ServiceUnavailable, SessionExpired) as exc:
            graph_available = False
            candidates = []
            logger.warning(
                "order_discovery_graph_unavailable_using_source_fallback",
                extra={
                    "anchor_type": anchor_type.value,
                    "error_type": type(exc).__name__,
                },
            )

        if candidates:
            return candidates

        progressive = self._return_configuration.discovery.progressive
        source_fallback_allowed = self._is_strong_anchor(anchor_type) or (
            progressive.weak_anchor_source_fallback_enabled
            and anchor_type.value in progressive.fuzzy_search_anchors
        )
        if not source_fallback_allowed:
            return []

        documents = await self._source_documents(anchor_type, anchor_value)
        candidates = [
            candidate for document in documents if (candidate := self._source_candidate(document))
        ]
        should_repair_graph = graph_available and (
            self._is_strong_anchor(anchor_type)
            or progressive.weak_anchor_targeted_graph_upsert_enabled
        )
        if candidates and should_repair_graph:
            try:
                await self._targeted_graph_upsert(candidates)
            except (Neo4jError, ServiceUnavailable, SessionExpired) as exc:
                logger.warning(
                    "order_discovery_graph_repair_deferred",
                    extra={
                        "anchor_type": anchor_type.value,
                        "candidate_count": len(candidates),
                        "error_type": type(exc).__name__,
                    },
                )
        return candidates

    def _assess_candidates(
        self,
        payload: StartAssociateConversationRequest,
        candidates: list[OrderCandidate],
    ) -> tuple[list[OrderCandidate], dict[str, Any], OrderSource]:
        evidence_key = {
            AnchorType.ORDER_NUMBER: "order_number",
            AnchorType.CUSTOMER_ID: "customer_id",
            AnchorType.PHONE: "phone_or_email",
            AnchorType.EMAIL: "phone_or_email",
            AnchorType.TRACKING_NUMBER: "tracking_number",
            AnchorType.SKU: "product_model",
            AnchorType.CUSTOMER_NAME: "customer_name",
            AnchorType.PRODUCT_DESCRIPTION: "product_model",
        }[payload.anchorType]
        candidate_by_id: dict[str, OrderCandidate] = {}
        inputs: list[DiscoveryCandidateInput] = []
        for candidate in candidates:
            candidate_id = f"{candidate.customerReference}:{candidate.orderReference}"
            candidate_by_id[candidate_id] = candidate
            inputs.append(
                DiscoveryCandidateInput(
                    candidateId=candidate_id,
                    orderReference=candidate.orderReference,
                    customerReference=candidate.customerReference,
                    orderSource=candidate.orderSource,
                    matchedAnchors=(payload.anchorType.value,),
                    evidenceReferences=(f"{candidate.evidenceSource}:{candidate.orderReference}",),
                )
            )
        assessment = self._order_discovery_agent.assess(
            DiscoveryAssessmentRequest(
                suppliedEvidence={evidence_key: payload.anchorValue},
                candidates=tuple(inputs),
            )
        )
        ranked: list[OrderCandidate] = []
        for result in assessment.rankedCandidates:
            found_candidate = candidate_by_id.get(result.candidateId)
            if found_candidate is not None:
                ranked.append(
                    found_candidate.model_copy(
                        update={"confidenceMillionths": result.scoreMillionths}
                    )
                )
        return ranked, assessment.model_dump(mode="json"), assessment.orderSource

    async def start(
        self,
        payload: StartAssociateConversationRequest,
        *,
        actor_id: str,
    ) -> AssociateConversationView:
        await self.ensure_indexes()
        conv_config = self._return_configuration.discovery.conversation
        if self._is_greeting(payload.anchorValue):
            candidates = []
            discovery_assessment: dict[str, Any] = {}
            order_source = OrderSource.UNKNOWN
            assistant_text = conv_config.greeting_response
            status = conv_config.greeting_status
            next_question = conv_config.greeting_next_question
            masked_value = conv_config.greeting_title
            dialogue_state = (
                self._return_configuration.discovery.progressive.dialogue_states.no_candidates
            )
            requested_slots: list[str] = []
            candidate_set_id = None
            candidate_set_expires_at = None
        else:
            candidates = await self._discover_candidates(
                payload.anchorType,
                payload.anchorValue,
            )
            candidates, discovery_assessment, order_source = self._assess_candidates(
                payload, candidates
            )
            (
                dialogue_state,
                requested_slots,
                candidate_set_id,
                candidate_set_expires_at,
                disambiguation_question,
            ) = self._dialogue_projection(candidates)
            if candidates:
                assistant_text = conv_config.initial_match_template.format(
                    count=len(candidates),
                    anchor_type=self._format_anchor_type(payload.anchorType),
                    anchor_value=payload.anchorValue,
                )
                status = "DISCOVERY_READY"
                next_question = (
                    disambiguation_question
                    or discovery_assessment.get("nextQuestion")
                    or conv_config.default_discovery_question
                )
                if requested_slots:
                    status = "DISCOVERY_CLARIFICATION_REQUIRED"
                    assistant_text = (
                        "I found multiple source-backed matches and need one detail "
                        "to narrow them safely."
                    )
            else:
                assistant_text = conv_config.initial_no_match_template.format(
                    anchor_type=self._format_anchor_type(payload.anchorType),
                    anchor_value=payload.anchorValue,
                )
                status = "NO_MATCH"
                next_question = conv_config.default_no_match_question
            masked_value = _mask(payload.anchorValue, payload.anchorType)
        now = _now()
        conversation_id = str(uuid.uuid4())
        initial_messages = [
            self._message(
                "ASSOCIATE",
                payload.anchorValue,
            ),
            self._message("AI_ASSISTANT", assistant_text),
        ]
        document: dict[str, Any] = {
            "_id": conversation_id,
            "status": status,
            "anchorType": payload.anchorType.value,
            "anchorValueMasked": masked_value,
            "orderSource": order_source.value,
            "discoveryAssessment": discovery_assessment,
            "anchorDigest": _digest(
                {"type": payload.anchorType.value, "value": payload.anchorValue}
            ),
            "messages": initial_messages,
            "lastMessageSequence": len(initial_messages),
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
            "discoveryLock": None,
            "returnDetails": None,
            "returnSessionId": None,
            "nextQuestion": next_question,
            "activeDialogueState": dialogue_state,
            "activeRequestedSlots": requested_slots,
            "clarificationPrompt": self._clarification_prompt_payload(
                candidates,
                requested_slots,
                next_question,
            ),
            "candidateSetId": candidate_set_id,
            "candidateSetExpiresAt": candidate_set_expires_at,
            "configurationReleaseId": self._configuration_release_id,
            "configurationChecksum": self._configuration_checksum,
            "configurationSource": self._configuration_source,
            "version": 0,
            "createdBy": actor_id,
            "createdAt": now,
            "updatedAt": now,
        }
        await self._conversations.insert_one(document)
        await self._persist_messages(
            conversation_id, starting_sequence=1, messages=initial_messages
        )
        discovery_snapshot_id = await self._persist_discovery_snapshot(
            conversation_id=conversation_id,
            snapshot_type="DISCOVERY_CANDIDATES",
            payload={
                "anchorType": payload.anchorType.value,
                "anchorValueMasked": document["anchorValueMasked"],
                "orderSource": order_source.value,
                "candidates": document["candidates"],
                "assessment": discovery_assessment,
            },
            actor_id=actor_id,
        )
        await self._conversations.update_one(
            {"_id": conversation_id},
            {"$set": {"discoverySnapshotId": discovery_snapshot_id}},
        )
        document["discoverySnapshotId"] = discovery_snapshot_id
        decision = discovery_assessment.get("decision")
        if isinstance(decision, dict):
            await self._repository.persist_agent_decision(
                aggregate_id=str(document["_id"]),
                session_id=None,
                decision=decision,
                decision_key=f"discovery:{document['anchorDigest']}",
                actor_id=actor_id,
            )
        return self._view(document)

    async def start_chat(
        self,
        payload: AssociateChatTurnRequest,
        *,
        actor_id: str,
    ) -> AssociateConversationView:
        lookup = await self._resolve_discovery_intent(payload.message)
        conversation = await self.start(lookup, actor_id=actor_id)
        return await self._apply_chat_copy(
            conversation,
            raw_message=payload.message,
        )

    async def list(self, limit: int = 100) -> list[AssociateConversationView]:
        documents = await self._conversations.find({}).sort("createdAt", -1).limit(limit).to_list()
        return [self._view(document) for document in documents]

    async def get(self, conversation_id: str) -> AssociateConversationView | None:
        document = await self._conversations.find_one({"_id": conversation_id})
        return None if document is None else self._view(document)

    async def continue_discovery(
        self,
        conversation_id: str,
        payload: ContinueAssociateConversationRequest,
        *,
        actor_id: str,
    ) -> AssociateConversationView:
        conversation = await self.get(conversation_id)
        if conversation is None:
            raise KeyError(conversation_id)
        if conversation.version != payload.expectedVersion:
            raise RuntimeError("Conversation version conflict")
        if conversation.discoveryLock is not None:
            raise ValueError("Discovery is already locked for this conversation")

        conv_config = self._return_configuration.discovery.conversation
        if self._is_greeting(payload.anchorValue):
            candidates = []
            discovery_assessment: dict[str, Any] = {}
            order_source = OrderSource.UNKNOWN
            assistant_text = conv_config.greeting_response
            status = conv_config.greeting_status
            next_question = conv_config.greeting_next_question
            dialogue_state = (
                self._return_configuration.discovery.progressive.dialogue_states.no_candidates
            )
            requested_slots: list[str] = []
            candidate_set_id = None
            candidate_set_expires_at = None
        else:
            lookup = StartAssociateConversationRequest(
                anchorType=payload.anchorType,
                anchorValue=payload.anchorValue,
            )
            candidates = await self._discover_candidates(
                lookup.anchorType,
                lookup.anchorValue,
            )
            candidates, discovery_assessment, order_source = self._assess_candidates(
                lookup, candidates
            )
            (
                dialogue_state,
                requested_slots,
                candidate_set_id,
                candidate_set_expires_at,
                disambiguation_question,
            ) = self._dialogue_projection(candidates)
            if candidates:
                assistant_text = conv_config.continue_match_template.format(
                    count=len(candidates),
                    anchor_type=self._format_anchor_type(payload.anchorType),
                    anchor_value=payload.anchorValue,
                )
                status = "DISCOVERY_READY"
                next_question = (
                    disambiguation_question
                    or discovery_assessment.get("nextQuestion")
                    or conv_config.default_discovery_question
                )
                if requested_slots:
                    status = "DISCOVERY_CLARIFICATION_REQUIRED"
                    assistant_text = (
                        "I found multiple source-backed matches and need one detail "
                        "to narrow them safely."
                    )
            else:
                assistant_text = conv_config.continue_no_match_template.format(
                    anchor_type=self._format_anchor_type(payload.anchorType),
                    anchor_value=payload.anchorValue,
                )
                status = "NO_MATCH"
                next_question = conv_config.default_continue_no_match_question

        messages = [
            self._message(
                "ASSOCIATE",
                f"{payload.anchorType.value}: {payload.anchorValue}",
            ),
            self._message("AI_ASSISTANT", assistant_text),
        ]
        updated = await self._conversations.find_one_and_update(
            {"_id": conversation_id, "version": payload.expectedVersion},
            {
                "$set": {
                    "status": status,
                    "anchorType": payload.anchorType.value,
                    "anchorValueMasked": _mask(payload.anchorValue, payload.anchorType),
                    "anchorDigest": _digest(
                        {"type": payload.anchorType.value, "value": payload.anchorValue}
                    ),
                    "orderSource": order_source.value,
                    "discoveryAssessment": discovery_assessment,
                    "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
                    "nextQuestion": next_question,
                    "activeDialogueState": dialogue_state,
                    "activeRequestedSlots": requested_slots,
                    "clarificationPrompt": self._clarification_prompt_payload(
                        candidates,
                        requested_slots,
                        next_question,
                    ),
                    "candidateSetId": candidate_set_id,
                    "candidateSetExpiresAt": candidate_set_expires_at,
                    "updatedAt": _now(),
                },
                "$push": {"messages": {"$each": messages}},
                "$inc": {"version": 1, "lastMessageSequence": len(messages)},
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise RuntimeError("Conversation version conflict")
        ending_sequence = int(str(updated.get("lastMessageSequence", 0)))
        await self._persist_messages(
            conversation_id,
            starting_sequence=ending_sequence - len(messages) + 1,
            messages=messages,
        )
        snapshot_id = await self._persist_discovery_snapshot(
            conversation_id=conversation_id,
            snapshot_type="DISCOVERY_FOLLOW_UP",
            payload={
                "anchorType": payload.anchorType.value,
                "anchorValueMasked": _mask(payload.anchorValue, payload.anchorType),
                "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
                "assessment": discovery_assessment,
            },
            actor_id=actor_id,
        )
        updated["discoverySnapshotId"] = snapshot_id
        await self._conversations.update_one(
            {"_id": conversation_id},
            {"$set": {"discoverySnapshotId": snapshot_id}},
        )
        return self._view(cast(dict[str, Any], updated))

    async def _continue_requested_slot(
        self,
        conversation: AssociateConversationView,
        payload: AssociateChatTurnRequest,
        *,
        actor_id: str,
    ) -> AssociateConversationView:
        """Bind one associate answer to the currently requested deterministic slot."""

        if payload.expectedVersion != conversation.version:
            raise RuntimeError("Conversation version conflict")
        if not conversation.activeRequestedSlots:
            raise ValueError("Conversation is not requesting a disambiguation slot")
        if _is_expired(conversation.candidateSetExpiresAt):
            raise RuntimeError("Candidate set expired; restart order discovery")

        slot = conversation.activeRequestedSlots[0]
        attributes = self._return_configuration.discovery.progressive.disambiguation_attributes
        attribute = next((item for item in attributes if item.slot == slot), None)
        if attribute is None:
            raise RuntimeError(f"Unsupported requested slot: {slot}")

        matched = [
            candidate
            for candidate in conversation.candidates
            if self._slot_response_matches(
                self._candidate_value(candidate, attribute.candidate_field),
                payload.message,
            )
        ]

        dialogue_states = self._return_configuration.discovery.progressive.dialogue_states
        excluded = set(conversation.activeRequestedSlots)
        next_attribute = self._select_disambiguation_attribute(
            matched,
            excluded_slots=excluded,
        )
        if not matched:
            assistant_text = (
                f"I could not match that {slot.replace('_', ' ')} to the current candidates."
            )
            next_state = conversation.activeDialogueState
            next_slots = conversation.activeRequestedSlots
            next_question = attribute.question
            next_candidates = conversation.candidates
        elif len(matched) > 1 and next_attribute is not None:
            next_slot, next_question = next_attribute
            assistant_text = f"I narrowed the result to {len(matched)} candidates."
            next_state = dialogue_states.slot_disambiguation
            next_slots = [next_slot]
            next_candidates = matched
        elif len(matched) > 1:
            next_question = (
                self._return_configuration.discovery.conversation.default_discovery_question
            )
            assistant_text = f"I narrowed the result to {len(matched)} orders. {next_question}"
            next_state = dialogue_states.generic_disambiguation
            next_slots = []
            next_candidates = matched
        else:
            next_question = (
                self._return_configuration.discovery.conversation.default_discovery_question
            )
            assistant_text = f"I found the matching customer and order. {next_question}"
            next_state = dialogue_states.single_candidate
            next_slots = []
            next_candidates = matched

        messages = [
            self._message("ASSOCIATE", payload.message),
            self._message("AI_ASSISTANT", assistant_text),
        ]
        updated = await self._conversations.find_one_and_update(
            {"_id": conversation.id, "version": payload.expectedVersion},
            {
                "$set": {
                    "status": (
                        "DISCOVERY_CLARIFICATION_REQUIRED" if next_slots else "DISCOVERY_READY"
                    ),
                    "candidates": [item.model_dump(mode="json") for item in next_candidates],
                    "activeDialogueState": next_state,
                    "activeRequestedSlots": next_slots,
                    "nextQuestion": next_question,
                    "clarificationPrompt": self._clarification_prompt_payload(
                        next_candidates,
                        next_slots,
                        next_question,
                    ),
                    "updatedAt": _now(),
                },
                "$push": {"messages": {"$each": messages}},
                "$inc": {"version": 1, "lastMessageSequence": len(messages)},
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise RuntimeError("Conversation version conflict")

        ending_sequence = int(str(updated.get("lastMessageSequence", 0)))
        await self._persist_messages(
            conversation.id,
            starting_sequence=ending_sequence - len(messages) + 1,
            messages=messages,
        )
        snapshot_id = await self._persist_discovery_snapshot(
            conversation_id=conversation.id,
            snapshot_type="DISCOVERY_SLOT_BOUND",
            payload={
                "slot": slot,
                "candidateSetId": conversation.candidateSetId,
                "candidateCount": len(next_candidates),
                "nextState": next_state,
                "nextRequestedSlots": next_slots,
            },
            actor_id=actor_id,
        )
        await self._conversations.update_one(
            {"_id": conversation.id},
            {"$set": {"discoverySnapshotId": snapshot_id}},
        )
        updated["discoverySnapshotId"] = snapshot_id
        return self._view(cast(dict[str, Any], updated))

    async def continue_chat(
        self,
        conversation_id: str,
        payload: AssociateChatTurnRequest,
        *,
        actor_id: str,
    ) -> AssociateConversationView:
        if payload.expectedVersion is None:
            raise ValueError("expectedVersion is required when continuing a conversation")
        existing = await self.get(conversation_id)
        if existing is None:
            raise KeyError(conversation_id)
        if existing.activeRequestedSlots:
            conversation = await self._continue_requested_slot(
                existing,
                payload,
                actor_id=actor_id,
            )
            if conversation.activeRequestedSlots:
                return await self._apply_chat_copy(
                    conversation,
                    raw_message=payload.message,
                )
            return conversation
        lookup = await self._resolve_discovery_intent(payload.message)
        conversation = await self.continue_discovery(
            conversation_id,
            ContinueAssociateConversationRequest(
                anchorType=lookup.anchorType,
                anchorValue=lookup.anchorValue,
                expectedVersion=payload.expectedVersion,
            ),
            actor_id=actor_id,
        )
        return await self._apply_chat_copy(
            conversation,
            raw_message=payload.message,
        )

    async def confirm(
        self,
        conversation_id: str,
        payload: ConfirmDiscoveryRequest,
        *,
        actor_id: str,
    ) -> AssociateConversationView:
        conversation = await self.get(conversation_id)
        if conversation is None:
            raise KeyError(conversation_id)
        if conversation.discoveryLock is not None:
            return conversation
        if conversation.version != payload.expectedVersion:
            raise RuntimeError("Conversation version conflict")
        if _is_expired(conversation.candidateSetExpiresAt):
            raise RuntimeError("Candidate set expired; restart order discovery")
        if (
            conversation.candidateSetId is not None
            and payload.candidateSetId != conversation.candidateSetId
        ):
            raise RuntimeError("Candidate set version conflict")
        if payload.candidateIndex >= len(conversation.candidates):
            raise ValueError("candidateIndex is out of range")
        candidate = conversation.candidates[payload.candidateIndex]
        line = next(
            (item for item in candidate.lines if item.orderLineId == payload.orderLineId), None
        )
        if line is None:
            raise ValueError("orderLineId does not belong to the selected order")
        lock_payload = {
            "customerReference": candidate.customerReference,
            "orderReference": candidate.orderReference,
            "sourceWebOrderNumber": candidate.sourceWebOrderNumber,
            "trilogieOrderNumber": candidate.trilogieOrderNumber,
            "orderSource": candidate.orderSource,
            "orderLineId": line.orderLineId,
            "productId": line.productId,
        }
        lock_key = f"{candidate.orderReference}:{line.orderLineId}"
        lock = DiscoveryLock(
            customerReference=candidate.customerReference,
            orderReference=candidate.orderReference,
            sourceWebOrderNumber=candidate.sourceWebOrderNumber,
            trilogieOrderNumber=candidate.trilogieOrderNumber,
            orderSource=candidate.orderSource,
            orderLineId=line.orderLineId,
            productId=line.productId,
            lockDigest=_digest(lock_payload),
            confirmedBy=actor_id,
            confirmedAt=_now(),
        )
        inserted_lock_id: str | None = None
        try:
            inserted_lock_id = str(uuid.uuid4())
            await self._locks.insert_one(
                {
                    "_id": inserted_lock_id,
                    **lock.model_dump(mode="json"),
                    "conversationId": conversation_id,
                    "lockKey": lock_key,
                    "status": "ACTIVE",
                    "expiresAt": _now() + timedelta(minutes=30),
                    "returnSessionId": None,
                }
            )
        except DuplicateKeyError as error:
            existing = await self._locks.find_one({"lockKey": lock_key, "status": "ACTIVE"})
            if existing is None or existing.get("conversationId") != conversation_id:
                raise RuntimeError(
                    "Order line is already locked by another return session"
                ) from error
        conv_config = self._return_configuration.discovery.conversation
        confirmation_messages = [
            self._message(
                "ASSOCIATE",
                conv_config.confirmation_associate_template.format(
                    order_reference=candidate.orderReference,
                    order_line_id=line.orderLineId,
                ),
            ),
            self._message(
                "AI_ASSISTANT",
                conv_config.confirmation_assistant_template.format(
                    order_line_id=line.orderLineId,
                ),
            ),
        ]
        updated = await self._conversations.find_one_and_update(
            {"_id": conversation_id, "version": payload.expectedVersion, "discoveryLock": None},
            {
                "$set": {
                    "status": "DETAILS_REQUIRED",
                    "orderSource": (
                        candidate.orderSource.value
                        if candidate.orderSource is not OrderSource.UNKNOWN
                        else conversation.orderSource.value
                    ),
                    "discoveryLock": lock.model_dump(mode="json"),
                    "activeDialogueState": "CONFIRMED",
                    "activeRequestedSlots": [],
                    "clarificationPrompt": None,
                    "nextQuestion": conv_config.default_details_question,
                    "updatedAt": _now(),
                },
                "$push": {"messages": {"$each": confirmation_messages}},
                "$inc": {
                    "version": 1,
                    "lastMessageSequence": len(confirmation_messages),
                },
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            if inserted_lock_id is not None:
                await self._locks.update_one(
                    {
                        "_id": inserted_lock_id,
                        "conversationId": conversation_id,
                        "returnSessionId": None,
                        "status": "ACTIVE",
                    },
                    {
                        "$set": {
                            "status": "CANCELLED",
                            "cancelledAt": _now(),
                            "cancellationReason": "CONVERSATION_VERSION_CONFLICT",
                        }
                    },
                )
            raise RuntimeError("Conversation version conflict")
        ending_sequence = int(str(updated.get("lastMessageSequence", 0)))
        await self._persist_messages(
            conversation_id,
            starting_sequence=ending_sequence - len(confirmation_messages) + 1,
            messages=confirmation_messages,
        )
        confirmation_snapshot_id = await self._persist_discovery_snapshot(
            conversation_id=conversation_id,
            snapshot_type="DISCOVERY_CONFIRMED",
            payload={
                "lock": lock.model_dump(mode="json"),
                "candidate": candidate.model_dump(mode="json"),
                "line": line.model_dump(mode="json"),
            },
            actor_id=actor_id,
        )
        await self._conversations.update_one(
            {"_id": conversation_id},
            {"$set": {"confirmationSnapshotId": confirmation_snapshot_id}},
        )
        updated["confirmationSnapshotId"] = confirmation_snapshot_id
        view = self._view(cast(dict[str, Any], updated))
        question = await self._generate_smart_question(view)
        assistant_index = max(0, len(view.messages) - 1)
        assistant_sequence = max(1, view.lastMessageSequence)
        planned_response = f"{view.messages[-1].content} {question}"
        await self._conversations.update_one(
            {"_id": conversation_id, "version": view.version},
            {
                "$set": {
                    f"messages.{assistant_index}.content": planned_response,
                    "nextQuestion": question,
                    "updatedAt": _now(),
                }
            },
        )
        await self._messages.update_one(
            {"conversationId": conversation_id, "sequence": assistant_sequence},
            {"$set": {"messageText": planned_response}},
        )
        messages = list(view.messages)
        messages[-1] = messages[-1].model_copy(update={"content": planned_response})
        return view.model_copy(update={"nextQuestion": question, "messages": messages})

    async def submit_details(
        self,
        conversation_id: str,
        payload: ReturnDetailsRequest,
        *,
        actor_id: str,
        correlation_id: str,
    ) -> tuple[AssociateConversationView, ReturnSessionView]:
        conversation = await self.get(conversation_id)
        if conversation is None:
            raise KeyError(conversation_id)
        if conversation.version != payload.expectedVersion:
            raise RuntimeError("Conversation version conflict")
        lock = conversation.discoveryLock
        if lock is None:
            raise ValueError("Discovery must be confirmed before collecting return details")
        selected_line = next(
            (
                item
                for candidate in conversation.candidates
                if candidate.orderReference == lock.orderReference
                for item in candidate.lines
                if item.orderLineId == lock.orderLineId
            ),
            None,
        )
        if selected_line is None:
            raise RuntimeError("Locked order line is absent from the sealed discovery context")
        if (
            selected_line.shippedQuantity is not None
            and payload.returnQuantity > selected_line.shippedQuantity
        ):
            raise ValueError("Return quantity exceeds the shipped quantity for the locked line")
        selected_candidate = next(
            (
                candidate
                for candidate in conversation.candidates
                if candidate.orderReference == lock.orderReference
            ),
            None,
        )
        if selected_candidate is None:
            raise RuntimeError("Locked order candidate is absent from discovery context")
        resolved_branch_reference = (
            payload.branchReference
            or selected_candidate.sellWarehouseId
            or selected_candidate.shipFromWarehouseId
        )
        order_source = (
            selected_candidate.orderSource
            if selected_candidate.orderSource is not OrderSource.UNKNOWN
            else conversation.orderSource
        )
        workflow_assessment = self._return_workflow_agent.assess(
            ReturnWorkflowAssessmentRequest(
                sessionId=conversation_id,
                orderSource=order_source,
                productPresence=payload.productPresence,
                proposedReturnMethod=payload.shippingPathExpectation,
                branchId=resolved_branch_reference,
                associateId=payload.associateReference or actor_id,
                items=(
                    ReturnItemInput(
                        orderLineId=lock.orderLineId,
                        productId=lock.productId,
                        requestedQuantity=payload.returnQuantity,
                        shippedQuantity=(
                            int(selected_line.shippedQuantity)
                            if selected_line.shippedQuantity is not None
                            else None
                        ),
                        reasonCode=payload.reasonCode,
                        attachmentIds=tuple(payload.attachmentIds),
                    ),
                ),
                pickupAssessment=payload.pickupAssessment,
            )
        )
        if not workflow_assessment.complete:
            raise ValueError(
                "Return details are incomplete: " + ", ".join(workflow_assessment.missingFields)
            )
        details = {
            **payload.model_dump(mode="json", exclude={"expectedVersion"}),
            "supportDraft": workflow_assessment.supportDraft,
            "agentDecision": workflow_assessment.decision.model_dump(mode="json"),
        }
        session = await self._repository.create_return(
            ReturnCreateRequest(
                customerReference=lock.customerReference,
                orderReference=lock.orderReference,
                itemReferences=[lock.orderLineId],
                productReferences=[lock.productId],
                processingWarehouseReference=next(
                    (
                        candidate.sellWarehouseId or candidate.shipFromWarehouseId
                        for candidate in conversation.candidates
                        if candidate.orderReference == lock.orderReference
                    ),
                    None,
                ),
                productType=selected_line.productType,
                reasonCode=payload.reasonCode,
                returnQuantity=payload.returnQuantity,
                packageCount=payload.packageCount,
                shippingPathExpectation=workflow_assessment.recommendedReturnMethod.value,
                orderSource=order_source.value,
                sourceWebOrderNumber=selected_candidate.sourceWebOrderNumber,
                trilogieOrderNumber=(selected_candidate.trilogieOrderNumber or lock.orderReference),
                productPresence=payload.productPresence.value,
                branchReference=resolved_branch_reference,
                associateReference=payload.associateReference or actor_id,
                pickupAssessment=payload.pickupAssessment,
                assumptionSetVersion=self._return_configuration.assumption_set_version,
                notes=payload.notes,
                channel="ASSOCIATE",
                idempotencyKey=f"associate:{conversation_id}:{lock.lockDigest}",
            ),
            correlation_id=correlation_id,
            actor_id=actor_id,
        )
        await self._repository.persist_return_intake_records(
            session_id=session.id,
            order_line_id=lock.orderLineId,
            product_id=lock.productId,
            reason_code=payload.reasonCode,
            requested_quantity=payload.returnQuantity,
            approved_method=workflow_assessment.recommendedReturnMethod.value,
            product_presence=payload.productPresence.value,
            package_count=payload.packageCount,
            pickup_assessment=payload.pickupAssessment,
            attachment_ids=payload.attachmentIds,
            actor_id=actor_id,
        )
        await self._repository.persist_agent_decision(
            aggregate_id=session.id,
            session_id=session.id,
            decision=workflow_assessment.decision.model_dump(mode="json"),
            decision_key=f"return-workflow:{conversation_id}:v{payload.expectedVersion}",
            actor_id=actor_id,
        )
        request_snapshot_id, snapshot_digest = await self._persist_return_request_snapshot(
            session_id=session.id,
            conversation_id=conversation_id,
            payload={
                "discoveryLock": lock.model_dump(mode="json"),
                "returnDetails": details,
                "returnSession": session.model_dump(mode="json"),
            },
            actor_id=actor_id,
        )
        await self._repository.update_return(
            session.id,
            {"returnRequestSnapshotId": request_snapshot_id},
        )
        await self._return_support.create_work_item(
            CreateSupportWorkItemRequest(
                sessionId=session.id,
                subject=f"Return request for order {lock.orderReference}",
                supportDraft=workflow_assessment.supportDraft,
                requestSnapshotDigest=snapshot_digest,
                idempotencyKey=f"support:{session.id}:{snapshot_digest}",
            ),
            actor_id=actor_id,
            correlation_id=correlation_id,
        )
        session = await self._repository.get_return(session.id) or session
        await self._locks.update_one(
            {"lockDigest": lock.lockDigest, "conversationId": conversation_id, "status": "ACTIVE"},
            {
                "$set": {
                    "returnSessionId": session.id,
                    "expiresAt": _now() + timedelta(days=7),
                }
            },
        )
        conv_config = self._return_configuration.discovery.conversation
        submission_messages = [
            self._message(
                "ASSOCIATE",
                conv_config.submission_associate_template,
            ),
            self._message(
                "AI_ASSISTANT",
                conv_config.submission_assistant_template.format(
                    session_id=session.id,
                ),
            ),
        ]
        updated = await self._conversations.find_one_and_update(
            {"_id": conversation_id, "version": payload.expectedVersion},
            {
                "$set": {
                    "status": "SUBMITTED",
                    "returnDetails": details,
                    "returnSessionId": session.id,
                    "nextQuestion": None,
                    "updatedAt": _now(),
                },
                "$push": {"messages": {"$each": submission_messages}},
                "$inc": {
                    "version": 1,
                    "lastMessageSequence": len(submission_messages),
                },
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise RuntimeError("Conversation version conflict")
        ending_sequence = int(str(updated.get("lastMessageSequence", 0)))
        await self._persist_messages(
            conversation_id,
            starting_sequence=ending_sequence - len(submission_messages) + 1,
            messages=submission_messages,
        )
        return self._view(cast(dict[str, Any], updated)), session

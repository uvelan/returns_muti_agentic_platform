"""MongoDB-backed operational repositories with optimistic concurrency."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast

from fastapi import HTTPException, Request
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

from return_platform.configuration.settings import Settings
from return_platform.operations.models import (
    AIGatewaySettingsView,
    AIRequestStatus,
    AITraceView,
    ReturnCreateRequest,
    ReturnSessionView,
    ReturnStatus,
    SeedStatusView,
    SupportCaseStatus,
    SupportCaseView,
    SupportOperationRequest,
    TimelineEvent,
    utc_now,
)
from return_platform.operations.seed_manifest import (
    SEED_CUSTOMERS,
    SEED_PRODUCTS,
    SEED_SCENARIOS,
    manifest_digest,
    materialize_domain_seed,
    materialize_seed,
    scenario_counts,
)
from return_platform.resources import RuntimeResources

RETURNS: Final = "operational_returns"
EVENTS: Final = "operational_events"
SUPPORT_CASES: Final = "support_cases"
AI_TRACES: Final = "ai_gateway_traces"
AI_SETTINGS: Final = "ai_gateway_settings"
AI_RATE_LIMITS: Final = "ai_gateway_rate_limits"
WORKER_HEARTBEATS: Final = "worker_heartbeats"
SEED_METADATA: Final = "seed_metadata"
SOURCE_ORDERS: Final = "orders"
SOURCE_CUSTOMERS: Final = "customers"
SOURCE_PRODUCTS: Final = "products"
DOMAIN_SOURCE_COLLECTIONS: Final = (
    "salesInv",
    "customerOutboundCDM",
    "shipmentInfo",
    "lkpSearchProduct",
)


class ConcurrencyConflictError(RuntimeError):
    pass


class OperationalRepository:
    """Repository for product-facing projections and immutable event evidence."""

    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        settings: Settings,
        source_client: AsyncMongoClient[dict[str, object]] | None = None,
    ) -> None:
        self._client = client
        self._source_client = source_client or client
        self._settings = settings
        self._db = client[settings.mongo_database]
        self._source_db = self._source_client[settings.source_mongo_database]
        self.returns = self._db[RETURNS]
        self.events = self._db[EVENTS]
        self.support_cases = self._db[SUPPORT_CASES]
        self.ai_traces = self._db[AI_TRACES]
        self.ai_settings = self._db[AI_SETTINGS]
        self.ai_rate_limits = self._db[AI_RATE_LIMITS]
        self.worker_heartbeats = self._db[WORKER_HEARTBEATS]
        self.seed_metadata = self._db[SEED_METADATA]

    @property
    def platform_client(self) -> AsyncMongoClient[dict[str, object]]:
        """Expose the shared client without leaking collection internals."""
        return self._client

    @property
    def source_client(self) -> AsyncMongoClient[dict[str, object]]:
        """Expose the read/source client for governed cross-store services."""
        return self._source_client

    async def ensure_indexes(self) -> None:
        await self.returns.create_index([("createdAt", DESCENDING)])
        await self.returns.create_index([("status", ASCENDING), ("updatedAt", ASCENDING)])
        await self.returns.create_index("idempotencyKey", unique=True, sparse=True)
        await self.events.create_index(
            [("streamId", ASCENDING), ("sequence", ASCENDING)], unique=True
        )
        await self.events.create_index(
            [("streamId", ASCENDING), ("deduplicationKey", ASCENDING)],
            unique=True,
            sparse=True,
        )
        await self.events.create_index([("publishedAt", ASCENDING), ("occurredAt", ASCENDING)])
        await self.support_cases.create_index(
            [("status", ASCENDING), ("priorityRank", ASCENDING), ("slaDueAt", ASCENDING)]
        )
        await self.support_cases.create_index("sessionId", unique=True, sparse=True)
        await self.ai_traces.create_index([("createdAt", DESCENDING)])
        await self.ai_traces.create_index([("sessionId", ASCENDING), ("createdAt", DESCENDING)])
        await self.worker_heartbeats.create_index("expiresAt", expireAfterSeconds=0)
        await self.ai_rate_limits.create_index("expiresAt", expireAfterSeconds=0)

    @staticmethod
    def _return_view(document: dict[str, Any]) -> ReturnSessionView:
        payload = {
            key: value for key, value in document.items() if key in ReturnSessionView.model_fields
        }
        payload["id"] = str(document["_id"])
        return ReturnSessionView.model_validate(payload)

    @staticmethod
    def _event_view(document: dict[str, Any]) -> TimelineEvent:
        payload = {
            key: value for key, value in document.items() if key in TimelineEvent.model_fields
        }
        payload["id"] = str(document["_id"])
        return TimelineEvent.model_validate(payload)

    @staticmethod
    def _support_view(document: dict[str, Any]) -> SupportCaseView:
        payload = {
            key: value for key, value in document.items() if key in SupportCaseView.model_fields
        }
        payload["id"] = str(document["_id"])
        status = str(document.get("status", ""))
        due_at = document.get("slaDueAt")
        payload["slaDueAt"] = due_at or document.get("createdAt")
        payload["slaBreached"] = bool(
            status in {SupportCaseStatus.OPEN.value, SupportCaseStatus.ASSIGNED.value}
            and isinstance(due_at, datetime)
            and utc_now() > due_at.astimezone(UTC)
        )
        return SupportCaseView.model_validate(payload)

    @staticmethod
    def _trace_view(document: dict[str, Any]) -> AITraceView:
        payload = {key: value for key, value in document.items() if key in AITraceView.model_fields}
        payload["id"] = str(document["_id"])
        return AITraceView.model_validate(payload)

    async def create_return(
        self,
        payload: ReturnCreateRequest,
        *,
        correlation_id: str,
        actor_id: str,
    ) -> ReturnSessionView:
        now = utc_now()
        session_id = str(uuid.uuid4())
        document: dict[str, Any] = {
            "_id": session_id,
            "correlationId": correlation_id,
            "workflowId": None,
            "customerReference": payload.customerReference,
            "orderReference": payload.orderReference,
            "itemReferences": payload.itemReferences,
            "productReferences": payload.productReferences or list(payload.itemReferences),
            "processingWarehouseReference": payload.processingWarehouseReference,
            "productType": payload.productType,
            "reasonCode": payload.reasonCode,
            "returnQuantity": payload.returnQuantity,
            "packageCount": payload.packageCount,
            "shippingPathExpectation": payload.shippingPathExpectation,
            "notes": payload.notes,
            "channel": payload.channel,
            "status": ReturnStatus.QUEUED.value,
            "currentStage": "INTAKE",
            "progressPercentage": 0,
            "eligibilityDecision": None,
            "returnReference": None,
            "supportTicketReference": None,
            "trackingReference": None,
            "bayReference": None,
            "feedbackReference": None,
            "supportCaseId": None,
            "aiRequestId": None,
            "failureCode": None,
            "failureMessage": None,
            "version": 0,
            "lastEventSequence": 0,
            "orchestrationState": "QUEUED",
            "orchestrationOwner": None,
            "orchestrationLeaseUntil": None,
            "idempotencyKey": payload.idempotencyKey,
            "createdBy": actor_id,
            "createdAt": now,
            "updatedAt": now,
        }
        try:
            await self.returns.insert_one(document)
        except DuplicateKeyError:
            if payload.idempotencyKey is None:
                raise
            existing = await self.returns.find_one({"idempotencyKey": payload.idempotencyKey})
            if existing is None:
                raise
            return self._return_view(cast(dict[str, Any], existing))
        await self.append_event(
            session_id,
            event_type="RETURN_REQUEST_ACCEPTED",
            actor_type="USER",
            actor_id=actor_id,
            payload={
                "orderReference": payload.orderReference,
                "itemCount": len(payload.itemReferences),
                "reasonCode": payload.reasonCode,
                "returnQuantity": payload.returnQuantity,
                "packageCount": payload.packageCount,
                "shippingPathExpectation": payload.shippingPathExpectation,
            },
        )
        stored = await self.returns.find_one({"_id": session_id})
        assert stored is not None
        return self._return_view(cast(dict[str, Any], stored))

    async def get_return(self, session_id: str) -> ReturnSessionView | None:
        document = await self.returns.find_one({"_id": session_id})
        return None if document is None else self._return_view(cast(dict[str, Any], document))

    async def list_returns(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ReturnSessionView]:
        query: dict[str, Any] = {} if status is None else {"status": status}
        cursor = self.returns.find(query).sort("createdAt", DESCENDING).limit(limit)
        return [self._return_view(cast(dict[str, Any], document)) async for document in cursor]

    async def update_return(
        self,
        session_id: str,
        updates: dict[str, Any],
        *,
        expected_version: int | None = None,
    ) -> ReturnSessionView:
        query: dict[str, Any] = {"_id": session_id}
        if expected_version is not None:
            query["version"] = expected_version
        update = {"$set": {**updates, "updatedAt": utc_now()}, "$inc": {"version": 1}}
        document = await self.returns.find_one_and_update(
            query,
            update,
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            exists = await self.returns.find_one({"_id": session_id}, {"_id": 1})
            if exists is None:
                raise KeyError(session_id)
            raise ConcurrencyConflictError(session_id)
        return self._return_view(cast(dict[str, Any], document))

    async def claim_next_return(
        self, worker_id: str, lease_seconds: int = 30
    ) -> ReturnSessionView | None:
        now = utc_now()
        lease_until = now + timedelta(seconds=lease_seconds)
        document = await self.returns.find_one_and_update(
            {
                "status": {
                    "$nin": [
                        ReturnStatus.COMPLETED.value,
                        ReturnStatus.CANCELLED.value,
                        ReturnStatus.FAILED.value,
                    ]
                },
                "orchestrationState": {"$in": ["QUEUED", "RUNNING"]},
                "$or": [
                    {"orchestrationLeaseUntil": None},
                    {"orchestrationLeaseUntil": {"$lt": now}},
                    {"orchestrationOwner": worker_id},
                ],
            },
            {
                "$set": {
                    "orchestrationState": "RUNNING",
                    "orchestrationOwner": worker_id,
                    "orchestrationLeaseUntil": lease_until,
                    "updatedAt": now,
                }
            },
            sort=[("updatedAt", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        return None if document is None else self._return_view(cast(dict[str, Any], document))

    async def release_return(self, session_id: str, state: str) -> None:
        await self.returns.update_one(
            {"_id": session_id},
            {
                "$set": {
                    "orchestrationState": state,
                    "orchestrationOwner": None,
                    "orchestrationLeaseUntil": None,
                    "updatedAt": utc_now(),
                },
                "$inc": {"version": 1},
            },
        )

    async def release_discovery_lock(self, session_id: str, *, reason: str) -> None:
        """Release only the active discovery lock bound to this return session."""
        now = utc_now()
        await self._db["discovery_locks"].update_many(
            {"returnSessionId": session_id, "status": "ACTIVE"},
            {
                "$set": {
                    "status": "RELEASED",
                    "releasedAt": now,
                    "releaseReason": reason,
                    "expiresAt": now,
                }
            },
        )

    async def append_event(
        self,
        stream_id: str,
        *,
        event_type: str,
        actor_type: str,
        actor_id: str,
        payload: dict[str, Any],
        deduplication_key: str | None = None,
    ) -> TimelineEvent:
        if deduplication_key is not None:
            existing = await self.events.find_one(
                {"streamId": stream_id, "deduplicationKey": deduplication_key}
            )
            if existing is not None:
                return self._event_view(cast(dict[str, Any], existing))
        now = utc_now()
        owner = await self.returns.find_one_and_update(
            {"_id": stream_id},
            {"$inc": {"lastEventSequence": 1}},
            projection={"lastEventSequence": 1},
            return_document=ReturnDocument.AFTER,
        )
        if owner is None:
            raise KeyError(stream_id)
        sequence = int(str(owner["lastEventSequence"]))
        event_id = f"{stream_id}:{sequence}"
        document: dict[str, Any] = {
            "_id": event_id,
            "streamId": stream_id,
            "sequence": sequence,
            "eventType": event_type,
            "actorType": actor_type,
            "actorId": actor_id,
            "payload": payload,
            "occurredAt": now,
            "publishedAt": None,
        }
        if deduplication_key is not None:
            document["deduplicationKey"] = deduplication_key
        try:
            await self.events.insert_one(document)
        except DuplicateKeyError:
            if deduplication_key is None:
                raise
            existing = await self.events.find_one(
                {"streamId": stream_id, "deduplicationKey": deduplication_key}
            )
            if existing is None:
                raise
            return self._event_view(cast(dict[str, Any], existing))
        return self._event_view(document)

    async def list_events(
        self,
        stream_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1_000,
    ) -> list[TimelineEvent]:
        cursor = (
            self.events.find({"streamId": stream_id, "sequence": {"$gt": after_sequence}})
            .sort("sequence", ASCENDING)
            .limit(limit)
        )
        return [self._event_view(cast(dict[str, Any], document)) async for document in cursor]

    async def list_unpublished_events(self, limit: int = 100) -> list[TimelineEvent]:
        cursor = self.events.find({"publishedAt": None}).sort("occurredAt", ASCENDING).limit(limit)
        return [self._event_view(cast(dict[str, Any], document)) async for document in cursor]

    async def mark_event_published(self, event_id: str) -> None:
        await self.events.update_one(
            {"_id": event_id, "publishedAt": None}, {"$set": {"publishedAt": utc_now()}}
        )

    async def consume_ai_quota(self, bucket: str) -> bool:
        now = utc_now()
        minute = now.replace(second=0, microsecond=0)
        quota_id = f"{bucket}:{minute.isoformat()}"
        try:
            document = await self.ai_rate_limits.find_one_and_update(
                {"_id": quota_id, "count": {"$lt": self._settings.ai_requests_per_minute}},
                {
                    "$inc": {"count": 1},
                    "$setOnInsert": {
                        "bucket": bucket,
                        "windowStartedAt": minute,
                        "expiresAt": minute + timedelta(minutes=2),
                    },
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            return False
        return document is not None

    async def create_ai_trace(
        self,
        *,
        session_id: str | None,
        status: AIRequestStatus,
        prompt_version: str,
        redacted_input: dict[str, Any],
        system_prompt: str,
        request_digest: str,
        original_request_digest: str | None = None,
    ) -> AITraceView:
        now = utc_now()
        trace_id = str(uuid.uuid4())
        document: dict[str, Any] = {
            "_id": trace_id,
            "sessionId": session_id,
            "status": status.value,
            "provider": None,
            "model": None,
            "promptVersion": prompt_version,
            "redactedInput": redacted_input,
            "systemPrompt": system_prompt,
            "requestDigest": request_digest,
            "responseText": None,
            "decision": None,
            "explanation": None,
            "confidenceMillionths": None,
            "latencyMs": None,
            "inputTokens": None,
            "outputTokens": None,
            "totalTokens": None,
            "responseDigest": None,
            "attempts": 0,
            "errorCode": None,
            "interceptedBy": None,
            "interceptionReason": None,
            "originalRequestDigest": original_request_digest,
            "version": 0,
            "createdAt": now,
            "updatedAt": now,
        }
        await self.ai_traces.insert_one(document)
        return self._trace_view(document)

    async def get_ai_trace(self, trace_id: str) -> AITraceView | None:
        document = await self.ai_traces.find_one({"_id": trace_id})
        return None if document is None else self._trace_view(cast(dict[str, Any], document))

    async def list_ai_traces(
        self, *, status: str | None = None, limit: int = 200
    ) -> list[AITraceView]:
        query: dict[str, Any] = {} if status is None else {"status": status}
        cursor = self.ai_traces.find(query).sort("createdAt", DESCENDING).limit(limit)
        return [self._trace_view(cast(dict[str, Any], document)) async for document in cursor]

    async def update_ai_trace(
        self,
        trace_id: str,
        updates: dict[str, Any],
        *,
        expected_version: int | None = None,
    ) -> AITraceView:
        query: dict[str, Any] = {"_id": trace_id}
        if expected_version is not None:
            query["version"] = expected_version
        document = await self.ai_traces.find_one_and_update(
            query,
            {"$set": {**updates, "updatedAt": utc_now()}, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            exists = await self.ai_traces.find_one({"_id": trace_id}, {"_id": 1})
            if exists is None:
                raise KeyError(trace_id)
            raise ConcurrencyConflictError(trace_id)
        return self._trace_view(cast(dict[str, Any], document))

    async def create_support_case(
        self,
        *,
        session_id: str,
        case_type: str,
        priority: str,
        reason: str,
    ) -> SupportCaseView:
        now = utc_now()
        case_id = str(uuid.uuid4())
        priority_rank = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}.get(priority, 2)
        sla_hours = {"CRITICAL": 1, "HIGH": 4, "NORMAL": 24, "LOW": 72}.get(priority, 24)
        document: dict[str, Any] = {
            "_id": case_id,
            "sessionId": session_id,
            "caseType": case_type,
            "status": SupportCaseStatus.OPEN.value,
            "priority": priority,
            "priorityRank": priority_rank,
            "reason": reason,
            "slaDueAt": now + timedelta(hours=sla_hours),
            "assignedTo": None,
            "resolution": None,
            "decision": None,
            "version": 0,
            "createdAt": now,
            "updatedAt": now,
        }
        try:
            await self.support_cases.insert_one(document)
        except DuplicateKeyError:
            existing = await self.support_cases.find_one({"sessionId": session_id})
            if existing is None:
                raise
            existing_status = str(existing.get("status", ""))
            if existing_status in {SupportCaseStatus.OPEN.value, SupportCaseStatus.ASSIGNED.value}:
                return self._support_view(cast(dict[str, Any], existing))
            reopened = await self.support_cases.find_one_and_update(
                {"_id": existing["_id"], "version": existing.get("version", 0)},
                {
                    "$set": {
                        "caseType": case_type,
                        "status": SupportCaseStatus.OPEN.value,
                        "priority": priority,
                        "priorityRank": priority_rank,
                        "reason": reason,
                        "slaDueAt": now + timedelta(hours=sla_hours),
                        "assignedTo": None,
                        "resolution": None,
                        "decision": None,
                        "updatedAt": now,
                    },
                    "$inc": {"version": 1},
                },
                return_document=ReturnDocument.AFTER,
            )
            if reopened is None:
                raise ConcurrencyConflictError(session_id) from None
            await self.update_return(session_id, {"supportCaseId": str(reopened["_id"])})
            return self._support_view(cast(dict[str, Any], reopened))
        await self.update_return(session_id, {"supportCaseId": case_id})
        return self._support_view(document)

    async def get_support_case(self, case_id: str) -> SupportCaseView | None:
        document = await self.support_cases.find_one({"_id": case_id})
        return None if document is None else self._support_view(cast(dict[str, Any], document))

    async def get_support_case_for_session(self, session_id: str) -> SupportCaseView | None:
        document = await self.support_cases.find_one({"sessionId": session_id})
        return None if document is None else self._support_view(cast(dict[str, Any], document))

    async def list_support_cases(
        self, status: str | None = None, limit: int = 200
    ) -> list[SupportCaseView]:
        query: dict[str, Any] = {} if status is None else {"status": status}
        cursor = (
            self.support_cases.find(query)
            .sort([("priorityRank", ASCENDING), ("slaDueAt", ASCENDING)])
            .limit(limit)
        )
        return [self._support_view(cast(dict[str, Any], document)) async for document in cursor]

    async def update_support_case(
        self,
        case_id: str,
        updates: dict[str, Any],
        *,
        expected_version: int,
    ) -> SupportCaseView:
        document = await self.support_cases.find_one_and_update(
            {"_id": case_id, "version": expected_version},
            {"$set": {**updates, "updatedAt": utc_now()}, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            exists = await self.support_cases.find_one({"_id": case_id}, {"_id": 1})
            if exists is None:
                raise KeyError(case_id)
            raise ConcurrencyConflictError(case_id)
        return self._support_view(cast(dict[str, Any], document))

    async def operate_support_case(
        self,
        case_id: str,
        payload: SupportOperationRequest,
        *,
        actor_id: str,
    ) -> SupportCaseView:
        """Apply a support command atomically across case, return, trace, event, and audit."""

        async def transaction(session: Any) -> dict[str, Any]:
            case_document = await self.support_cases.find_one(
                {"_id": case_id, "version": payload.expectedVersion},
                session=session,
            )
            if case_document is None:
                exists = await self.support_cases.find_one(
                    {"_id": case_id}, {"_id": 1}, session=session
                )
                if exists is None:
                    raise KeyError(case_id)
                raise ConcurrencyConflictError(case_id)

            operation = payload.operation
            session_id = str(case_document["sessionId"])
            now = utc_now()
            case_updates: dict[str, Any]
            if case_document.get("status") not in {
                SupportCaseStatus.OPEN.value,
                SupportCaseStatus.ASSIGNED.value,
            }:
                raise ValueError("Only open or assigned support cases can be operated.")

            if operation == "ASSIGN":
                if not payload.assignee:
                    raise ValueError("ASSIGN requires assignee")
                case_updates = {
                    "status": SupportCaseStatus.ASSIGNED.value,
                    "assignedTo": payload.assignee,
                }
            elif operation in {"APPROVE", "REJECT"}:
                return_document = await self.returns.find_one({"_id": session_id}, session=session)
                if return_document is None or not return_document.get("aiRequestId"):
                    raise ValueError("Case has no AI request to resolve")
                trace_id = str(return_document["aiRequestId"])
                trace_result = await self.ai_traces.update_one(
                    {
                        "_id": trace_id,
                        "status": {"$ne": AIRequestStatus.MANUAL_OVERRIDE.value},
                    },
                    {
                        "$set": {
                            "status": AIRequestStatus.MANUAL_OVERRIDE.value,
                            "decision": operation,
                            "explanation": payload.reason,
                            "confidenceMillionths": 1_000_000,
                            "provider": "MANUAL",
                            "model": "support-override-v1",
                            "interceptedBy": actor_id,
                            "interceptionReason": payload.reason,
                            "updatedAt": now,
                        },
                        "$inc": {"version": 1},
                    },
                    session=session,
                )
                if trace_result.matched_count != 1:
                    raise ValueError("AI request is missing")
                await self.returns.update_one(
                    {"_id": session_id},
                    {
                        "$set": {
                            "status": ReturnStatus.RUNNING.value,
                            "orchestrationState": "QUEUED",
                            "orchestrationOwner": None,
                            "orchestrationLeaseUntil": None,
                            "updatedAt": now,
                        },
                        "$inc": {"version": 1},
                    },
                    session=session,
                )
                case_updates = {
                    "status": SupportCaseStatus.RESOLVED.value,
                    "resolution": payload.reason,
                    "decision": operation,
                    "assignedTo": case_document.get("assignedTo") or actor_id,
                }
            elif operation in {"RETRY", "RESUME"}:
                await self.returns.update_one(
                    {"_id": session_id},
                    {
                        "$set": {
                            "status": ReturnStatus.QUEUED.value,
                            "orchestrationState": "QUEUED",
                            "orchestrationOwner": None,
                            "orchestrationLeaseUntil": None,
                            "failureCode": None,
                            "failureMessage": None,
                            "updatedAt": now,
                        },
                        "$inc": {"version": 1},
                    },
                    session=session,
                )
                case_updates = {
                    "status": SupportCaseStatus.RESOLVED.value,
                    "resolution": payload.reason,
                }
            elif operation == "CANCEL":
                await self.returns.update_one(
                    {"_id": session_id},
                    {
                        "$set": {
                            "status": ReturnStatus.CANCELLED.value,
                            "orchestrationState": "CANCELLED",
                            "updatedAt": now,
                        },
                        "$inc": {"version": 1},
                    },
                    session=session,
                )
                case_updates = {
                    "status": SupportCaseStatus.CANCELLED.value,
                    "resolution": payload.reason,
                }
            else:
                raise ValueError("Unsupported operation")

            updated_case = await self.support_cases.find_one_and_update(
                {"_id": case_id, "version": payload.expectedVersion},
                {"$set": {**case_updates, "updatedAt": now}, "$inc": {"version": 1}},
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if updated_case is None:
                raise ConcurrencyConflictError(case_id)

            owner = await self.returns.find_one_and_update(
                {"_id": session_id},
                {"$inc": {"lastEventSequence": 1}},
                projection={"lastEventSequence": 1},
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if owner is None:
                raise KeyError(session_id)
            sequence = int(str(owner["lastEventSequence"]))
            await self.events.insert_one(
                {
                    "_id": f"{session_id}:{sequence}",
                    "streamId": session_id,
                    "sequence": sequence,
                    "eventType": f"SUPPORT_{operation}",
                    "actorType": "SUPPORT",
                    "actorId": actor_id,
                    "payload": {"caseId": case_id, "reason": payload.reason},
                    "occurredAt": now,
                    "publishedAt": None,
                },
                session=session,
            )
            await self._db["audit"].insert_one(
                {
                    "_id": str(uuid.uuid4()),
                    "action": f"SUPPORT_{operation}",
                    "actor": actor_id,
                    "target": case_id,
                    "timestamp": now,
                    "details": {"sessionId": session_id, "reason": payload.reason},
                },
                session=session,
            )
            return cast(dict[str, Any], updated_case)

        async with self._client.start_session() as mongo_session:
            updated = await mongo_session.with_transaction(transaction)
        return self._support_view(updated)

    async def get_ai_settings(self) -> AIGatewaySettingsView:
        document = await self.ai_settings.find_one({"_id": "global"})
        if document is None:
            now = utc_now()
            document = {
                "_id": "global",
                "interceptMode": self._settings.ai_interception_default,
                "providerOrder": self._settings.ai_provider_order.split(","),
                "version": 0,
                "updatedAt": now,
                "updatedBy": "system",
            }
            try:
                await self.ai_settings.insert_one(document)
            except DuplicateKeyError:
                document = await self.ai_settings.find_one({"_id": "global"})
                assert document is not None
        return AIGatewaySettingsView.model_validate(
            {key: value for key, value in document.items() if key != "_id"}
        )

    async def update_ai_settings(
        self,
        *,
        intercept_mode: bool,
        provider_order: list[str],
        expected_version: int,
        actor_id: str,
    ) -> AIGatewaySettingsView:
        document = await self.ai_settings.find_one_and_update(
            {"_id": "global", "version": expected_version},
            {
                "$set": {
                    "interceptMode": intercept_mode,
                    "providerOrder": provider_order,
                    "updatedAt": utc_now(),
                    "updatedBy": actor_id,
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise ConcurrencyConflictError("global")
        return AIGatewaySettingsView.model_validate(
            {key: value for key, value in document.items() if key != "_id"}
        )

    async def append_audit(
        self,
        *,
        action: str,
        actor: str,
        target: str,
        details: dict[str, Any],
    ) -> None:
        await self._db["audit"].insert_one(
            {
                "_id": str(uuid.uuid4()),
                "action": action,
                "actor": actor,
                "target": target,
                "timestamp": utc_now(),
                "details": details,
            }
        )

    async def heartbeat(self, worker_name: str, instance_id: str, *, ttl_seconds: int) -> None:
        now = utc_now()
        await self.worker_heartbeats.update_one(
            {"_id": worker_name},
            {
                "$set": {
                    "instanceId": instance_id,
                    "lastSeenAt": now,
                    "expiresAt": now + timedelta(seconds=ttl_seconds * 3),
                }
            },
            upsert=True,
        )

    async def get_heartbeat(self, worker_name: str) -> dict[str, Any] | None:
        document = await self.worker_heartbeats.find_one({"_id": worker_name})
        return None if document is None else cast(dict[str, Any], document)

    async def source_order(self, order_reference: str) -> dict[str, Any] | None:
        sales_inventory = await self._source_db["salesInv"].find_one(
            {"salesHdrEventData.orderId": order_reference}
        )
        if sales_inventory is not None:
            raw = cast(dict[str, Any], sales_inventory)
            header_event = raw.get("salesHdrEventData")
            header = raw.get("salesHdr")
            header_event = header_event if isinstance(header_event, dict) else {}
            header = header if isinstance(header, dict) else {}
            header_data = header.get("salesHdrData")
            header_data = header_data if isinstance(header_data, dict) else {}
            items: list[dict[str, Any]] = []
            for sales_line in raw.get("salesLines", []):
                if not isinstance(sales_line, dict):
                    continue
                line_data = sales_line.get("lineData")
                if not isinstance(line_data, dict):
                    continue
                line_reference = str(
                    line_data.get("orderLineId") or f"{order_reference}:LINE:{len(items) + 1}"
                )
                items.append(
                    {
                        "itemReference": line_reference,
                        "productReference": str(
                            line_data.get("productId") or line_data.get("sku") or ""
                        ),
                        "productType": str(line_data.get("productType") or "STANDARD"),
                        "description": str(line_data.get("productDesc") or ""),
                        "orderedQuantity": int(line_data.get("orderQty") or 0),
                        "shippedQuantity": int(line_data.get("shipQty") or 0),
                    }
                )
            return {
                "_id": order_reference,
                "orderReference": order_reference,
                "customerReference": str(header_data.get("custId") or ""),
                "customerName": str(header_data.get("custName") or ""),
                "status": str(header_event.get("orderStatus") or "UNKNOWN"),
                "sellingWarehouseReference": str(header_event.get("sellWhseId") or ""),
                "shipFromWarehouseReference": str(header_event.get("shipFromWhseId") or ""),
                "deliveredAt": raw.get("deliveredAt"),
                "items": items,
                "sourceAssetId": "SOURCE_MONGODB_SALES_INV",
                "sourceDocumentReference": str(raw.get("_id") or order_reference),
            }

        # Transitional fallback for existing sandbox fixtures. New flows must seed salesInv.
        document = await self._source_db[SOURCE_ORDERS].find_one({"_id": order_reference})
        return None if document is None else cast(dict[str, Any], document)

    async def seed_status(self) -> SeedStatusView:
        seed_version = self._settings.seed_version
        expected_digest = manifest_digest(seed_version)
        metadata = await self.seed_metadata.find_one({"_id": seed_version})
        seeded_query = {"seedVersion": seed_version, "seedDigest": expected_digest}
        counts = {
            "customers": await self._source_db[SOURCE_CUSTOMERS].count_documents({}),
            "orders": await self._source_db[SOURCE_ORDERS].count_documents({}),
            "products": await self._source_db[SOURCE_PRODUCTS].count_documents({}),
            "seededCustomers": await self._source_db[SOURCE_CUSTOMERS].count_documents(
                seeded_query
            ),
            "seededOrders": await self._source_db[SOURCE_ORDERS].count_documents(seeded_query),
            "seededProducts": await self._source_db[SOURCE_PRODUCTS].count_documents(seeded_query),
            "salesInv": await self._source_db["salesInv"].count_documents(seeded_query),
            "customerOutboundCDM": await self._source_db["customerOutboundCDM"].count_documents(
                seeded_query
            ),
            "shipmentInfo": await self._source_db["shipmentInfo"].count_documents(seeded_query),
            "lkpSearchProduct": await self._source_db["lkpSearchProduct"].count_documents(
                seeded_query
            ),
            "returns": await self.returns.count_documents({}),
            "supportCases": await self.support_cases.count_documents({}),
            "aiTraces": await self.ai_traces.count_documents({}),
        }
        expected_counts = {
            "seededCustomers": len(SEED_CUSTOMERS),
            "seededOrders": len(SEED_SCENARIOS),
            "seededProducts": len(SEED_PRODUCTS),
            "salesInv": len(SEED_SCENARIOS),
            "customerOutboundCDM": len(SEED_CUSTOMERS),
            "shipmentInfo": len(SEED_SCENARIOS),
            "lkpSearchProduct": len(SEED_PRODUCTS),
        }
        errors = [
            f"{name} expected {expected}, found {counts[name]}."
            for name, expected in expected_counts.items()
            if counts[name] != expected
        ]
        metadata_digest = str(metadata.get("digest", "")) if metadata is not None else ""
        if metadata_digest != expected_digest:
            errors.append(
                "Seed metadata digest is absent or does not match the canonical manifest."
            )
        applied_at = metadata.get("appliedAt") if metadata is not None else None
        applied_by = metadata.get("appliedBy") if metadata is not None else None
        return SeedStatusView(
            version=seed_version,
            digest=metadata_digest,
            appliedAt=cast(datetime | None, applied_at),
            appliedBy=cast(str | None, applied_by),
            ready=not errors,
            counts=counts,
            scenarioCounts=scenario_counts(),
            validationErrors=errors,
        )

    async def apply_seed(self, *, actor_id: str) -> SeedStatusView:
        if self._settings.environment not in {"development", "test"}:
            raise PermissionError(
                "Deterministic source seed apply is restricted to development and test."
            )
        now = utc_now()
        seed_version = self._settings.seed_version
        digest = manifest_digest(seed_version)
        customers, products, orders = materialize_seed(seed_version, now)
        domain_records = materialize_domain_seed(seed_version, now)
        for collection, documents in (
            (self._source_db[SOURCE_CUSTOMERS], customers),
            (self._source_db[SOURCE_PRODUCTS], products),
            (self._source_db[SOURCE_ORDERS], orders),
            *((self._source_db[name], records) for name, records in domain_records.items()),
        ):
            for document in documents:
                await collection.replace_one({"_id": document["_id"]}, document, upsert=True)
        await self._source_db["salesInv"].create_index(
            "salesHdrEventData.orderId", unique=True, name="sales_order_number_unique"
        )
        await self._source_db["salesInv"].create_index(
            "salesHdr.salesHdrData.custId", name="sales_customer_lookup"
        )
        await self._source_db["salesInv"].create_index(
            "salesLines.lineData.productId", name="sales_product_lookup"
        )
        await self._source_db["salesInv"].create_index(
            "salesLines.lineData.sku", name="sales_sku_lookup"
        )
        await self._source_db["customerOutboundCDM"].create_index(
            "customerId", unique=True, name="customer_id_unique"
        )
        await self._source_db["customerOutboundCDM"].create_index(
            "phoneNumber", name="customer_phone_lookup"
        )
        await self._source_db["customerOutboundCDM"].create_index(
            "email", name="customer_email_lookup"
        )
        await self._source_db["shipmentInfo"].create_index(
            "shipmentInfoEventData.trkNum", unique=True, name="tracking_number_unique"
        )
        await self._source_db["lkpSearchProduct"].create_index(
            "productId", unique=True, name="product_id_unique"
        )
        await self._source_db["lkpSearchProduct"].create_index("sku", name="product_sku_lookup")
        await self.seed_metadata.replace_one(
            {"_id": seed_version},
            {
                "_id": seed_version,
                "digest": digest,
                "appliedAt": now,
                "appliedBy": actor_id,
                "scenarioCounts": scenario_counts(),
            },
            upsert=True,
        )
        return await self.seed_status()

    async def reset_demo_data(self) -> None:
        seed_version = self._settings.seed_version
        source_cleanup = {"seedVersion": seed_version}
        await self._source_db[SOURCE_CUSTOMERS].delete_many(source_cleanup)
        await self._source_db[SOURCE_PRODUCTS].delete_many(source_cleanup)
        await self._source_db[SOURCE_ORDERS].delete_many(source_cleanup)
        for collection_name in DOMAIN_SOURCE_COLLECTIONS:
            await self._source_db[collection_name].delete_many(source_cleanup)
        await self.seed_metadata.delete_many({"_id": seed_version})
        await self.returns.delete_many({})
        await self.events.delete_many({})
        await self.support_cases.delete_many({})
        await self.ai_traces.delete_many({})
        await self.ai_rate_limits.delete_many({})
        await self.worker_heartbeats.delete_many({})
        await self._db["return_sessions"].delete_many({})
        await self._db["return_session_audit_events"].delete_many({})
        await self._db["return_session_outbox_events"].delete_many({})
        await self._db["return_session_agent_decisions"].delete_many({})


def resolve_operational_repository(request: Request) -> OperationalRepository:
    resources = getattr(request.app.state, "resources", None)
    settings = getattr(request.app.state, "settings", None)
    if (
        not isinstance(resources, RuntimeResources)
        or resources.mongo is None
        or not isinstance(settings, Settings)
    ):
        raise HTTPException(status_code=503, detail="Platform MongoDB is unavailable")
    return OperationalRepository(resources.mongo, settings, resources.source_mongo)

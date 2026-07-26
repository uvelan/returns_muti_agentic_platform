"""Lease-based transactional outbox dispatcher for external integrations."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

import httpx
from pymongo import ASCENDING, AsyncMongoClient, ReturnDocument

from return_platform.configuration.settings import Settings

logger = logging.getLogger("return_platform.integration_outbox")


@dataclass(frozen=True, slots=True)
class OutboxCommand:
    id: str
    topic: str
    aggregate_type: str
    aggregate_id: str
    idempotency_key: str
    payload: dict[str, Any]
    attempt_count: int


@dataclass(frozen=True, slots=True)
class DispatchResult:
    external_reference: str | None
    response_digest: str | None


class TopicDispatcher(Protocol):
    async def dispatch(self, command: OutboxCommand) -> DispatchResult: ...


class ExternalDependencyNotConfigured(RuntimeError):
    """Raised when a topic has no approved external adapter configuration."""


class HttpTicketDispatcher:
    """HTTP adapter for an explicitly configured external Support mirror."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        if settings.support_ticket_base_url is None:
            raise ExternalDependencyNotConfigured("External Support base URL is not configured.")
        self._settings = settings
        self._client = client

    async def dispatch(self, command: OutboxCommand) -> DispatchResult:
        headers = {
            "Idempotency-Key": command.idempotency_key,
            "X-Correlation-ID": command.id,
        }
        if self._settings.support_ticket_api_key is not None:
            headers["Authorization"] = (
                f"Bearer {self._settings.support_ticket_api_key.get_secret_value()}"
            )
        response = await self._client.post(
            f"{self._settings.support_ticket_base_url}/tickets",
            json=command.payload,
            headers=headers,
            timeout=self._settings.operation_timeout_seconds,
        )
        if response.status_code == 429 or response.status_code >= 500:
            raise RuntimeError(f"RETRYABLE_HTTP_{response.status_code}")
        if response.status_code >= 400:
            raise ExternalDependencyNotConfigured(
                f"External Support rejected the contract with HTTP {response.status_code}."
            )
        body = response.content
        digest = hashlib.sha256(body).hexdigest()
        external_reference: str | None = None
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                raw = parsed.get("id") or parsed.get("ticketId") or parsed.get("reference")
                external_reference = str(raw) if raw is not None else None
        except ValueError:
            pass
        return DispatchResult(
            external_reference=external_reference,
            response_digest=digest,
        )


class HttpJsonDispatcher:
    """Generic approved JSON command adapter for OMC, carrier, or notifications."""

    def __init__(
        self,
        *,
        base_url: str,
        resource_path: str,
        client: httpx.AsyncClient,
        timeout_seconds: float,
        api_key: str | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/{resource_path.lstrip('/')}"
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._api_key = api_key

    async def dispatch(self, command: OutboxCommand) -> DispatchResult:
        headers = {
            "Idempotency-Key": command.idempotency_key,
            "X-Correlation-ID": command.id,
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        response = await self._client.post(
            self._url,
            json=command.payload,
            headers=headers,
            timeout=self._timeout_seconds,
        )
        if response.status_code == 429 or response.status_code >= 500:
            raise RuntimeError(f"RETRYABLE_HTTP_{response.status_code}")
        if response.status_code >= 400:
            raise ExternalDependencyNotConfigured(
                f"External contract rejected with HTTP {response.status_code}."
            )
        digest = hashlib.sha256(response.content).hexdigest()
        external_reference: str | None = None
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                raw = (
                    parsed.get("id")
                    or parsed.get("commandId")
                    or parsed.get("bookingId")
                    or parsed.get("notificationId")
                    or parsed.get("reference")
                )
                external_reference = str(raw) if raw is not None else None
        except ValueError:
            pass
        return DispatchResult(
            external_reference=external_reference,
            response_digest=digest,
        )


class IntegrationOutboxDispatcher:
    """Claim one outbox record, dispatch it, and persist auditable delivery state."""

    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        settings: Settings,
        dispatchers: dict[str, TopicDispatcher],
        *,
        worker_id: str | None = None,
    ) -> None:
        self._settings = settings
        self._collection = client[settings.mongo_database]["integration_outbox"]
        self._worker_id = worker_id or f"outbox-{uuid.uuid4()}"
        self._dispatchers = dispatchers

    async def ensure_indexes(self) -> None:
        await self._collection.create_index("idempotencyKey", unique=True)
        await self._collection.create_index(
            [("status", ASCENDING), ("nextAttemptAt", ASCENDING)]
        )
        await self._collection.create_index("leaseUntil")

    async def claim(self, *, lease_seconds: int = 60) -> OutboxCommand | None:
        now = datetime.now(UTC)
        document = await self._collection.find_one_and_update(
            {
                "status": {"$in": ["PENDING", "RETRY"]},
                "nextAttemptAt": {"$lte": now},
                "$or": [
                    {"leaseUntil": None},
                    {"leaseUntil": {"$exists": False}},
                    {"leaseUntil": {"$lt": now}},
                    {"leaseOwner": self._worker_id},
                ],
            },
            {
                "$set": {
                    "status": "DISPATCHING",
                    "leaseOwner": self._worker_id,
                    "leaseUntil": now + timedelta(seconds=lease_seconds),
                    "updatedAt": now,
                },
                "$inc": {"attemptCount": 1},
            },
            sort=[("nextAttemptAt", ASCENDING), ("createdAt", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            return None
        raw = cast(dict[str, Any], document)
        return OutboxCommand(
            id=str(raw["_id"]),
            topic=str(raw["topic"]),
            aggregate_type=str(raw["aggregateType"]),
            aggregate_id=str(raw["aggregateId"]),
            idempotency_key=str(raw["idempotencyKey"]),
            payload=cast(dict[str, Any], raw.get("payload", {})),
            attempt_count=int(raw.get("attemptCount", 1)),
        )

    async def _mark_delivered(self, command: OutboxCommand, result: DispatchResult) -> None:
        now = datetime.now(UTC)
        await self._collection.update_one(
            {"_id": command.id, "leaseOwner": self._worker_id},
            {
                "$set": {
                    "status": "DELIVERED",
                    "externalReference": result.external_reference,
                    "responseDigest": result.response_digest,
                    "deliveredAt": now,
                    "updatedAt": now,
                    "leaseOwner": None,
                    "leaseUntil": None,
                    "lastErrorCode": None,
                }
            },
        )

    async def _mark_failed(
        self,
        command: OutboxCommand,
        *,
        error_code: str,
        retryable: bool,
    ) -> None:
        now = datetime.now(UTC)
        delay_seconds = min(3_600, 2 ** min(command.attempt_count, 10))
        await self._collection.update_one(
            {"_id": command.id, "leaseOwner": self._worker_id},
            {
                "$set": {
                    "status": "RETRY" if retryable else "BLOCKED_EXTERNAL_DEPENDENCY",
                    "lastErrorCode": error_code[:128],
                    "nextAttemptAt": now + timedelta(seconds=delay_seconds),
                    "updatedAt": now,
                    "leaseOwner": None,
                    "leaseUntil": None,
                }
            },
        )

    async def dispatch_once(self) -> bool:
        command = await self.claim()
        if command is None:
            return False
        dispatcher = self._dispatchers.get(command.topic)
        if dispatcher is None:
            await self._mark_failed(
                command,
                error_code="ADAPTER_NOT_CONFIGURED",
                retryable=False,
            )
            return True
        try:
            result = await dispatcher.dispatch(command)
        except ExternalDependencyNotConfigured as error:
            await self._mark_failed(
                command,
                error_code=type(error).__name__,
                retryable=False,
            )
        except (httpx.TimeoutException, httpx.TransportError, RuntimeError) as error:
            logger.warning(
                "integration_outbox_dispatch_retry",
                extra={
                    "topic": command.topic,
                    "aggregate_id": command.aggregate_id,
                    "error_type": type(error).__name__,
                },
            )
            await self._mark_failed(
                command,
                error_code=type(error).__name__,
                retryable=True,
            )
        else:
            await self._mark_delivered(command, result)
        return True

    async def run_forever(self, *, idle_seconds: float = 1.0) -> None:
        await self.ensure_indexes()
        while True:
            processed = await self.dispatch_once()
            if not processed:
                await asyncio.sleep(idle_seconds)

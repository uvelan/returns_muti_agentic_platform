"""Explicit external dependency ports for the production return platform."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ExternalCommand:
    command_id: str
    idempotency_key: str
    correlation_id: str
    aggregate_id: str
    operation: str
    request_digest: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class ExternalCommandResult:
    status: str
    external_reference: str | None
    authoritative_version: str | None
    response_digest: str | None
    error_code: str | None = None


class OmcCommandGateway(Protocol):
    async def execute(self, command: ExternalCommand) -> ExternalCommandResult: ...


class CarrierGateway(Protocol):
    async def execute(self, command: ExternalCommand) -> ExternalCommandResult: ...


class ExternalTicketGateway(Protocol):
    async def execute(self, command: ExternalCommand) -> ExternalCommandResult: ...


class NotificationGateway(Protocol):
    async def execute(self, command: ExternalCommand) -> ExternalCommandResult: ...


class DocumentArtifactGateway(Protocol):
    async def execute(self, command: ExternalCommand) -> ExternalCommandResult: ...

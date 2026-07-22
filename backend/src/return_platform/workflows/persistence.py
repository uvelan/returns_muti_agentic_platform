"""Authoritative Platform MongoDB persistence for Return workflow sessions."""

import asyncio
import hashlib
import json
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Final, Literal, Never, Protocol, Self
from uuid import UUID

from pydantic import Field, ValidationError, model_validator
from pydantic_core import PydanticCustomError
from pymongo import AsyncMongoClient, ReadPreference, ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import (
    AutoReconnect,
    DuplicateKeyError,
    ExecutionTimeout,
    NetworkTimeout,
    OperationFailure,
    PyMongoError,
)
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from return_platform.canonical.base import (
    CanonicalBaseModel,
    CanonicalIdentifier,
    NonBlankText,
    Sha256Digest,
    UtcDateTime,
)
from return_platform.canonical.operations import (
    AgentDecision,
    AuditEvent,
    ContextSnapshot,
    ReturnSession,
    WorkflowStage,
)
from return_platform.workflows.stage_results import (
    StageContextBinding,
    StageResultValidationError,
    bay_assignment_result_from_binding,
    eligibility_result_from_binding,
    feedback_learning_result_from_binding,
    fulfillment_tracking_result_from_binding,
    return_request_result_from_binding,
)

__all__ = [
    "AppliedSessionCommand",
    "ReturnSessionDocument",
    "ReturnSessionOutboxEvent",
    "ReturnSessionPersistenceError",
    "ReturnSessionPersistenceErrorCode",
    "ReturnSessionPersistenceReceipt",
    "ReturnSessionPersistenceStatus",
    "ReturnSessionRepository",
    "ReturnSessionRepositoryPort",
    "ReturnSessionTransition",
]

_SCHEMA_VERSION: Final = "1.0"
_DIGEST_DOMAIN: Final = "return-platform:return-session-document:v1"
_DATABASE_PATTERN: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,62}$")
_COLLECTION_PATTERN: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,126}$")
_MIN_TIMEOUT_SECONDS: Final = 0.05
_MAX_TIMEOUT_SECONDS: Final = 30.0
_AUTH_ERROR_CODES: Final = frozenset({13, 18})
_UNKNOWN_COMMIT_RESULT_LABEL: Final = "UnknownTransactionCommitResult"
_NEXT_STAGE: Final = {
    WorkflowStage.INTAKE: WorkflowStage.ORDER_DISCOVERY,
    WorkflowStage.ORDER_DISCOVERY: WorkflowStage.ELIGIBILITY_EVALUATION,
    WorkflowStage.ELIGIBILITY_EVALUATION: WorkflowStage.RETURN_REQUEST,
    WorkflowStage.RETURN_REQUEST: WorkflowStage.FULFILLMENT_TRACKING,
    WorkflowStage.FULFILLMENT_TRACKING: WorkflowStage.BAY_ASSIGNMENT,
    WorkflowStage.BAY_ASSIGNMENT: WorkflowStage.FEEDBACK_LEARNING,
    WorkflowStage.FEEDBACK_LEARNING: WorkflowStage.COMPLETED,
}


class ReturnSessionPersistenceStatus(StrEnum):
    """Stable session persistence outcomes."""

    CREATED = "CREATED"
    ALREADY_PRESENT = "ALREADY_PRESENT"
    TRANSITIONED = "TRANSITIONED"
    ALREADY_APPLIED = "ALREADY_APPLIED"


class ReturnSessionPersistenceErrorCode(StrEnum):
    """Safe stable persistence errors."""

    INVALID_INPUT = "RETURN_SESSION_INVALID_INPUT"
    DOCUMENT_INVALID = "RETURN_SESSION_DOCUMENT_INVALID"
    IMMUTABLE_CONFLICT = "RETURN_SESSION_IMMUTABLE_CONFLICT"
    NOT_FOUND = "RETURN_SESSION_NOT_FOUND"
    STALE_STAGE = "RETURN_SESSION_STALE_STAGE"
    COMMAND_CONFLICT = "RETURN_SESSION_COMMAND_CONFLICT"
    AUTH_FAILED = "RETURN_SESSION_AUTH_FAILED"
    TIMEOUT = "RETURN_SESSION_TIMEOUT"
    WRITE_FAILED = "RETURN_SESSION_WRITE_FAILED"
    WRITE_OUTCOME_UNKNOWN = "RETURN_SESSION_WRITE_OUTCOME_UNKNOWN"
    READ_FAILED = "RETURN_SESSION_READ_FAILED"


_SAFE_MESSAGES: Final = {
    ReturnSessionPersistenceErrorCode.INVALID_INPUT: "The session persistence input is invalid.",
    ReturnSessionPersistenceErrorCode.DOCUMENT_INVALID: "The session document is invalid.",
    ReturnSessionPersistenceErrorCode.IMMUTABLE_CONFLICT: (
        "A different session document already exists."
    ),
    ReturnSessionPersistenceErrorCode.NOT_FOUND: "The Return session was not found.",
    ReturnSessionPersistenceErrorCode.STALE_STAGE: (
        "The Return session stage or revision is stale."
    ),
    ReturnSessionPersistenceErrorCode.COMMAND_CONFLICT: (
        "The transition command is bound to different evidence."
    ),
    ReturnSessionPersistenceErrorCode.AUTH_FAILED: (
        "Platform MongoDB rejected the configured principal."
    ),
    ReturnSessionPersistenceErrorCode.TIMEOUT: "The session operation exceeded its timeout.",
    ReturnSessionPersistenceErrorCode.WRITE_FAILED: "The session write failed.",
    ReturnSessionPersistenceErrorCode.WRITE_OUTCOME_UNKNOWN: (
        "The session write outcome is unknown."
    ),
    ReturnSessionPersistenceErrorCode.READ_FAILED: "The session read failed.",
}


class ReturnSessionPersistenceError(RuntimeError):
    """Sanitized session persistence failure."""

    def __init__(self, code: ReturnSessionPersistenceErrorCode) -> None:
        self.code = code
        self.safe_message = _SAFE_MESSAGES[code]
        super().__init__(self.safe_message)


def _raise_error(code: ReturnSessionPersistenceErrorCode) -> Never:
    raise ReturnSessionPersistenceError(code)


def _raise_model_error(error_type: str, message: str) -> Never:
    raise PydanticCustomError(error_type, message)


def _write_error_code(
    error: PyMongoError,
    default: ReturnSessionPersistenceErrorCode,
) -> ReturnSessionPersistenceErrorCode:
    if error.has_error_label(_UNKNOWN_COMMIT_RESULT_LABEL):
        return ReturnSessionPersistenceErrorCode.WRITE_OUTCOME_UNKNOWN
    if isinstance(error, OperationFailure) and error.code in _AUTH_ERROR_CODES:
        return ReturnSessionPersistenceErrorCode.AUTH_FAILED
    return default


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _state_digest(payload: object) -> str:
    encoded = _canonical_json(payload).encode("utf-8")
    digest = hashlib.sha256(usedforsecurity=False)
    domain = _DIGEST_DOMAIN.encode("ascii")
    digest.update(len(domain).to_bytes(4, "big"))
    digest.update(domain)
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)
    return digest.hexdigest()


class AppliedSessionCommand(CanonicalBaseModel):
    """Immutable command binding retained with authoritative session state."""

    command_id: UUID
    completed_stage: WorkflowStage
    resulting_stage: WorkflowStage
    resulting_revision: Annotated[int, Field(strict=True, ge=1)]


class ReturnSessionDocument(CanonicalBaseModel):
    """Digest-bound mutable aggregate persisted in Platform MongoDB."""

    schema_version: Literal["1.0"]
    document_id: CanonicalIdentifier
    revision: Annotated[int, Field(strict=True, ge=0)]
    session: ReturnSession
    applied_commands: Annotated[tuple[AppliedSessionCommand, ...], Field(max_length=32)]
    state_digest: Sha256Digest

    @classmethod
    def create(cls, session: ReturnSession) -> Self:
        """Create revision zero from one strict canonical session."""
        try:
            checked = ReturnSession.model_validate(session.model_dump(mode="python"))
        except (AttributeError, ValidationError) as error:
            raise ReturnSessionPersistenceError(
                ReturnSessionPersistenceErrorCode.INVALID_INPUT
            ) from error
        digest_payload = {
            "schema_version": _SCHEMA_VERSION,
            "document_id": f"RETURN_SESSION:{checked.session_id}",
            "revision": 0,
            "session": checked.model_dump(mode="json"),
            "applied_commands": [],
        }
        return cls.model_validate(
            {
                **digest_payload,
                "session": checked,
                "applied_commands": (),
                "state_digest": _state_digest(digest_payload),
            }
        )

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        """Validate identity, revision history, and digest binding."""
        if self.document_id != f"RETURN_SESSION:{self.session.session_id}":
            _raise_model_error(
                "return_session_document_identity_invalid",
                "document_id must be derived from session_id",
            )
        if len(self.applied_commands) != self.revision:
            _raise_model_error(
                "return_session_document_revision_invalid",
                "revision must equal the applied command count",
            )
        if self.applied_commands:
            revisions = tuple(item.resulting_revision for item in self.applied_commands)
            if revisions != tuple(range(1, self.revision + 1)):
                _raise_model_error(
                    "return_session_document_command_revision_invalid",
                    "applied command revisions must be contiguous",
                )
            if self.applied_commands[-1].resulting_stage is not self.session.current_stage:
                _raise_model_error(
                    "return_session_document_stage_invalid",
                    "the last command must produce the current session stage",
                )
        payload = {
            "schema_version": self.schema_version,
            "document_id": self.document_id,
            "revision": self.revision,
            "session": self.session.model_dump(mode="json"),
            "applied_commands": [item.model_dump(mode="json") for item in self.applied_commands],
        }
        if self.state_digest != _state_digest(payload):
            _raise_model_error(
                "return_session_document_digest_invalid",
                "state_digest does not match session state",
            )
        return self

    def to_mongo_document(self) -> dict[str, object]:
        """Return the fixed JSON-compatible MongoDB representation."""
        payload = self.model_dump(mode="json")
        return {"_id": self.document_id, **payload}

    @classmethod
    def from_mongo_document(cls, value: Mapping[str, object]) -> Self:
        """Fail closed while reconstructing stored session state."""
        normalized = {str(key): item for key, item in value.items()}
        stored_id = normalized.pop("_id", None)
        if not isinstance(stored_id, str) or normalized.get("document_id") != stored_id:
            _raise_error(ReturnSessionPersistenceErrorCode.DOCUMENT_INVALID)
        try:
            return cls.model_validate_json(_canonical_json(normalized))
        except ValidationError as error:
            raise ReturnSessionPersistenceError(
                ReturnSessionPersistenceErrorCode.DOCUMENT_INVALID
            ) from error


class ReturnSessionOutboxEvent(CanonicalBaseModel):
    """Immutable safe event written atomically with session state."""

    schema_version: Literal["1.0"] = "1.0"
    event_id: UUID
    session_id: UUID
    command_id: UUID | None
    event_type: Literal["RETURN_SESSION_CREATED", "RETURN_SESSION_STAGE_TRANSITIONED"]
    revision: Annotated[int, Field(strict=True, ge=0)]
    current_stage: WorkflowStage
    status: NonBlankText
    occurred_at: UtcDateTime
    published_at: UtcDateTime | None = None


class ReturnSessionTransition(CanonicalBaseModel):
    """Complete deterministic transition and evidence request."""

    command_id: UUID
    expected_revision: Annotated[int, Field(strict=True, ge=0)]
    completed_stage: WorkflowStage
    resulting_stage: WorkflowStage
    context_snapshot: ContextSnapshot | None = None
    agent_decision: AgentDecision | None = None
    updated_at: UtcDateTime
    audit_event: AuditEvent
    outbox_event: ReturnSessionOutboxEvent

    @model_validator(mode="after")
    def validate_transition_binding(self) -> Self:
        """Require the fixed next stage and bound evidence identities."""
        if _NEXT_STAGE.get(self.completed_stage) is not self.resulting_stage:
            _raise_model_error(
                "return_session_transition_stage_invalid",
                "resulting_stage must be the fixed successor",
            )
        if self.outbox_event.command_id != self.command_id:
            _raise_model_error(
                "return_session_transition_outbox_command_invalid",
                "outbox command identity must match",
            )
        if self.outbox_event.revision != self.expected_revision + 1:
            _raise_model_error(
                "return_session_transition_outbox_revision_invalid",
                "outbox revision must match the resulting revision",
            )
        if self.outbox_event.current_stage is not self.resulting_stage:
            _raise_model_error(
                "return_session_transition_outbox_stage_invalid",
                "outbox stage must match the resulting stage",
            )
        expected_context_schema = {
            WorkflowStage.INTAKE: "intake-v1",
            WorkflowStage.ORDER_DISCOVERY: "order-discovery-v1",
            WorkflowStage.ELIGIBILITY_EVALUATION: "eligibility-v1",
            WorkflowStage.RETURN_REQUEST: "return-request-v1",
            WorkflowStage.FULFILLMENT_TRACKING: "fulfillment-tracking-v1",
            WorkflowStage.BAY_ASSIGNMENT: "bay-assignment-v1",
            WorkflowStage.FEEDBACK_LEARNING: "feedback-learning-v1",
        }.get(self.completed_stage)
        if expected_context_schema is None:
            if self.context_snapshot is not None:
                _raise_model_error(
                    "return_session_transition_context_unexpected",
                    "context snapshot is not supported for this stage",
                )
        elif (
            self.context_snapshot is None
            or self.context_snapshot.schema_version != expected_context_schema
        ):
            _raise_model_error(
                "return_session_transition_context_required",
                "the stage requires its versioned context snapshot",
            )
        if self.completed_stage is WorkflowStage.ELIGIBILITY_EVALUATION:
            if (
                self.agent_decision is None
                or self.agent_decision.stage is not WorkflowStage.ELIGIBILITY_EVALUATION
            ):
                _raise_model_error(
                    "return_session_transition_decision_required",
                    "eligibility transition requires AgentDecision evidence",
                )
        elif self.agent_decision is not None:
            _raise_model_error(
                "return_session_transition_decision_unexpected",
                "AgentDecision is allowed only for eligibility evaluation",
            )
        return self


class ReturnSessionPersistenceReceipt(CanonicalBaseModel):
    """Validated create or transition receipt."""

    status: ReturnSessionPersistenceStatus
    session_id: UUID
    revision: Annotated[int, Field(strict=True, ge=0)]
    current_stage: WorkflowStage
    state_digest: Sha256Digest


class ReturnSessionRepositoryPort(Protocol):
    """Activity-facing persistence contract for injected adapters and tests."""

    async def initialize(
        self,
        session: ReturnSession,
        audit_event: AuditEvent,
        outbox_event: ReturnSessionOutboxEvent,
    ) -> ReturnSessionPersistenceReceipt: ...

    async def transition(
        self,
        session_id: UUID,
        transition: ReturnSessionTransition,
    ) -> ReturnSessionPersistenceReceipt: ...


class _ReturnSessionPersistencePort(Protocol):
    async def insert_bundle(
        self,
        session: dict[str, object],
        audit: dict[str, object],
        outbox: dict[str, object],
    ) -> None: ...

    async def transition_bundle(
        self,
        *,
        document_id: str,
        expected_revision: int,
        completed_stage: str,
        command_id: str,
        session: dict[str, object],
        audit: dict[str, object],
        outbox: dict[str, object],
        decision: dict[str, object] | None,
    ) -> tuple[Mapping[str, object], bool]: ...

    async def find_session(self, document_id: str) -> Mapping[str, object] | None: ...


class _PyMongoReturnSessionPersistence:
    """Execute one explicit no-hidden-retry transaction per state change."""

    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        sessions: AsyncCollection[dict[str, object]],
        audits: AsyncCollection[dict[str, object]],
        outbox: AsyncCollection[dict[str, object]],
        decisions: AsyncCollection[dict[str, object]],
    ) -> None:
        self._client = client
        self._sessions = sessions
        self._audits = audits
        self._outbox = outbox
        self._decisions = decisions

    async def insert_bundle(
        self,
        session: dict[str, object],
        audit: dict[str, object],
        outbox: dict[str, object],
    ) -> None:
        async with self._client.start_session() as mongo_session:
            async with await mongo_session.start_transaction(
                read_concern=ReadConcern("snapshot"),
                write_concern=WriteConcern(w="majority", j=True),
                read_preference=ReadPreference.PRIMARY,
            ):
                await self._sessions.insert_one(session, session=mongo_session)
                await self._audits.insert_one(audit, session=mongo_session)
                await self._outbox.insert_one(outbox, session=mongo_session)

    async def transition_bundle(
        self,
        *,
        document_id: str,
        expected_revision: int,
        completed_stage: str,
        command_id: str,
        session: dict[str, object],
        audit: dict[str, object],
        outbox: dict[str, object],
        decision: dict[str, object] | None,
    ) -> tuple[Mapping[str, object], bool]:
        async with self._client.start_session() as mongo_session:
            async with await mongo_session.start_transaction(
                read_concern=ReadConcern("snapshot"),
                write_concern=WriteConcern(w="majority", j=True),
                read_preference=ReadPreference.PRIMARY,
            ):
                updated = await self._sessions.find_one_and_replace(
                    {
                        "_id": document_id,
                        "revision": expected_revision,
                        "session.current_stage": completed_stage,
                        "applied_commands.command_id": {"$ne": command_id},
                    },
                    session,
                    return_document=ReturnDocument.AFTER,
                    session=mongo_session,
                )
                if updated is None:
                    current = await self._sessions.find_one(
                        {"_id": document_id}, session=mongo_session
                    )
                    if current is None:
                        _raise_error(ReturnSessionPersistenceErrorCode.NOT_FOUND)
                    return current, False
                await self._audits.insert_one(audit, session=mongo_session)
                await self._outbox.insert_one(outbox, session=mongo_session)
                if decision is not None:
                    await self._decisions.insert_one(decision, session=mongo_session)
                return updated, True

    async def find_session(self, document_id: str) -> Mapping[str, object] | None:
        return await self._sessions.find_one({"_id": document_id})


def _mongo_document(model: CanonicalBaseModel, identifier: UUID) -> dict[str, object]:
    return {"_id": str(identifier), **model.model_dump(mode="json")}


class ReturnSessionRepository:
    """Validate and persist authoritative session/audit/outbox state atomically."""

    def __init__(
        self,
        port: _ReturnSessionPersistencePort,
        *,
        operation_timeout_seconds: float,
    ) -> None:
        if (
            not isinstance(operation_timeout_seconds, float)
            or not _MIN_TIMEOUT_SECONDS <= operation_timeout_seconds <= _MAX_TIMEOUT_SECONDS
        ):
            _raise_error(ReturnSessionPersistenceErrorCode.INVALID_INPUT)
        self._port = port
        self._operation_timeout_seconds = operation_timeout_seconds

    @classmethod
    def from_client(
        cls,
        client: AsyncMongoClient[dict[str, object]],
        *,
        database: str,
        sessions_collection: str,
        audits_collection: str,
        outbox_collection: str,
        decisions_collection: str,
        operation_timeout_seconds: float,
    ) -> Self:
        names = (
            sessions_collection,
            audits_collection,
            outbox_collection,
            decisions_collection,
        )
        if (
            not isinstance(database, str)
            or _DATABASE_PATTERN.fullmatch(database) is None
            or len(set(names)) != len(names)
            or any(
                not isinstance(name, str)
                or name.startswith("system.")
                or "$" in name
                or _COLLECTION_PATTERN.fullmatch(name) is None
                for name in names
            )
        ):
            _raise_error(ReturnSessionPersistenceErrorCode.INVALID_INPUT)
        durable = {
            name: client[database][name].with_options(
                read_concern=ReadConcern("majority"),
                read_preference=ReadPreference.PRIMARY,
                write_concern=WriteConcern(w="majority", j=True),
            )
            for name in names
        }
        return cls(
            _PyMongoReturnSessionPersistence(
                client,
                durable[sessions_collection],
                durable[audits_collection],
                durable[outbox_collection],
                durable[decisions_collection],
            ),
            operation_timeout_seconds=operation_timeout_seconds,
        )

    async def _read(self, document_id: str) -> ReturnSessionDocument | None:
        try:
            async with asyncio.timeout(self._operation_timeout_seconds):
                stored = await self._port.find_session(document_id)
        except asyncio.CancelledError:
            raise
        except (AutoReconnect, ExecutionTimeout, NetworkTimeout, TimeoutError) as error:
            raise ReturnSessionPersistenceError(
                ReturnSessionPersistenceErrorCode.READ_FAILED
            ) from error
        except OperationFailure as error:
            code = (
                ReturnSessionPersistenceErrorCode.AUTH_FAILED
                if error.code in _AUTH_ERROR_CODES
                else ReturnSessionPersistenceErrorCode.READ_FAILED
            )
            raise ReturnSessionPersistenceError(code) from error
        except PyMongoError as error:
            raise ReturnSessionPersistenceError(
                ReturnSessionPersistenceErrorCode.READ_FAILED
            ) from error
        return None if stored is None else ReturnSessionDocument.from_mongo_document(stored)

    async def get(self, session_id: UUID) -> ReturnSessionDocument | None:
        """Read one authoritative session by exact identity."""
        if not isinstance(session_id, UUID):
            _raise_error(ReturnSessionPersistenceErrorCode.INVALID_INPUT)
        return await self._read(f"RETURN_SESSION:{session_id}")

    async def initialize(
        self,
        session: ReturnSession,
        audit_event: AuditEvent,
        outbox_event: ReturnSessionOutboxEvent,
    ) -> ReturnSessionPersistenceReceipt:
        """Create session, audit, and outbox evidence in one transaction."""
        document = ReturnSessionDocument.create(session)
        if (
            audit_event.session_id != session.session_id
            or outbox_event.session_id != session.session_id
            or outbox_event.command_id is not None
            or outbox_event.event_type != "RETURN_SESSION_CREATED"
            or outbox_event.revision != 0
            or outbox_event.current_stage is not session.current_stage
            or outbox_event.status != session.status
            or outbox_event.occurred_at != session.created_at
        ):
            _raise_error(ReturnSessionPersistenceErrorCode.INVALID_INPUT)
        created = True
        try:
            async with asyncio.timeout(self._operation_timeout_seconds):
                await self._port.insert_bundle(
                    document.to_mongo_document(),
                    _mongo_document(audit_event, audit_event.audit_event_id),
                    _mongo_document(outbox_event, outbox_event.event_id),
                )
        except asyncio.CancelledError:
            raise
        except DuplicateKeyError:
            created = False
            existing = await self._read(document.document_id)
            if existing != document:
                _raise_error(ReturnSessionPersistenceErrorCode.IMMUTABLE_CONFLICT)
        except (AutoReconnect, NetworkTimeout, TimeoutError) as error:
            raise ReturnSessionPersistenceError(
                ReturnSessionPersistenceErrorCode.WRITE_OUTCOME_UNKNOWN
            ) from error
        except ExecutionTimeout as error:
            raise ReturnSessionPersistenceError(
                _write_error_code(error, ReturnSessionPersistenceErrorCode.TIMEOUT)
            ) from error
        except OperationFailure as error:
            raise ReturnSessionPersistenceError(
                _write_error_code(error, ReturnSessionPersistenceErrorCode.WRITE_FAILED)
            ) from error
        except PyMongoError as error:
            raise ReturnSessionPersistenceError(
                _write_error_code(error, ReturnSessionPersistenceErrorCode.WRITE_FAILED)
            ) from error
        stored = await self._read(document.document_id)
        if stored != document:
            _raise_error(ReturnSessionPersistenceErrorCode.IMMUTABLE_CONFLICT)
        return ReturnSessionPersistenceReceipt(
            status=(
                ReturnSessionPersistenceStatus.CREATED
                if created
                else ReturnSessionPersistenceStatus.ALREADY_PRESENT
            ),
            session_id=session.session_id,
            revision=0,
            current_stage=session.current_stage,
            state_digest=document.state_digest,
        )

    async def transition(
        self,
        session_id: UUID,
        transition: ReturnSessionTransition,
    ) -> ReturnSessionPersistenceReceipt:
        """Compare and transition session/audit/outbox evidence atomically."""
        if not isinstance(session_id, UUID) or not isinstance(transition, ReturnSessionTransition):
            _raise_error(ReturnSessionPersistenceErrorCode.INVALID_INPUT)
        current = await self.get(session_id)
        if current is None:
            _raise_error(ReturnSessionPersistenceErrorCode.NOT_FOUND)
        for applied in current.applied_commands:
            if applied.command_id == transition.command_id:
                if (
                    applied.completed_stage is not transition.completed_stage
                    or applied.resulting_stage is not transition.resulting_stage
                ):
                    _raise_error(ReturnSessionPersistenceErrorCode.COMMAND_CONFLICT)
                return ReturnSessionPersistenceReceipt(
                    status=ReturnSessionPersistenceStatus.ALREADY_APPLIED,
                    session_id=session_id,
                    revision=current.revision,
                    current_stage=current.session.current_stage,
                    state_digest=current.state_digest,
                )
        if (
            current.revision != transition.expected_revision
            or current.session.current_stage is not transition.completed_stage
        ):
            _raise_error(ReturnSessionPersistenceErrorCode.STALE_STAGE)
        next_status = (
            "COMPLETED" if transition.resulting_stage is WorkflowStage.COMPLETED else "RUNNING"
        )
        completed_at = (
            transition.updated_at if transition.resulting_stage is WorkflowStage.COMPLETED else None
        )
        context_update: dict[str, object] = {}
        if transition.completed_stage is WorkflowStage.INTAKE:
            if current.session.intake_context is not None:
                _raise_error(ReturnSessionPersistenceErrorCode.STALE_STAGE)
            context_update["intake_context"] = transition.context_snapshot
        elif transition.completed_stage is WorkflowStage.ORDER_DISCOVERY:
            if current.session.discovery_context is not None:
                _raise_error(ReturnSessionPersistenceErrorCode.STALE_STAGE)
            context_update["discovery_context"] = transition.context_snapshot
        elif transition.completed_stage is WorkflowStage.ELIGIBILITY_EVALUATION:
            if current.session.eligibility_context is not None:
                _raise_error(ReturnSessionPersistenceErrorCode.STALE_STAGE)
            context_update["eligibility_context"] = transition.context_snapshot
        elif transition.completed_stage is WorkflowStage.RETURN_REQUEST:
            if (
                current.session.return_request_context is not None
                or current.session.eligibility_context is None
                or transition.context_snapshot is None
            ):
                _raise_error(ReturnSessionPersistenceErrorCode.STALE_STAGE)
            try:
                eligibility = eligibility_result_from_binding(
                    StageContextBinding(
                        completed_stage=WorkflowStage.ELIGIBILITY_EVALUATION,
                        schema_version=current.session.eligibility_context.schema_version,
                        payload_json=current.session.eligibility_context.payload_json,
                        payload_digest=current.session.eligibility_context.payload_digest,
                    )
                )
                return_request = return_request_result_from_binding(
                    StageContextBinding(
                        completed_stage=WorkflowStage.RETURN_REQUEST,
                        schema_version=transition.context_snapshot.schema_version,
                        payload_json=transition.context_snapshot.payload_json,
                        payload_digest=transition.context_snapshot.payload_digest,
                    )
                )
            except StageResultValidationError:
                _raise_error(ReturnSessionPersistenceErrorCode.INVALID_INPUT)
            if (
                return_request.eligibility_decision is not eligibility.decision
                or return_request.eligibility_context_digest
                != current.session.eligibility_context.payload_digest
            ):
                _raise_error(ReturnSessionPersistenceErrorCode.INVALID_INPUT)
            context_update["return_request_context"] = transition.context_snapshot
        elif transition.completed_stage is WorkflowStage.FULFILLMENT_TRACKING:
            if (
                current.session.fulfillment_tracking_context is not None
                or current.session.return_request_context is None
                or transition.context_snapshot is None
            ):
                _raise_error(ReturnSessionPersistenceErrorCode.STALE_STAGE)
            try:
                return_request = return_request_result_from_binding(
                    StageContextBinding(
                        completed_stage=WorkflowStage.RETURN_REQUEST,
                        schema_version=current.session.return_request_context.schema_version,
                        payload_json=current.session.return_request_context.payload_json,
                        payload_digest=current.session.return_request_context.payload_digest,
                    )
                )
                fulfillment = fulfillment_tracking_result_from_binding(
                    StageContextBinding(
                        completed_stage=WorkflowStage.FULFILLMENT_TRACKING,
                        schema_version=transition.context_snapshot.schema_version,
                        payload_json=transition.context_snapshot.payload_json,
                        payload_digest=transition.context_snapshot.payload_digest,
                    )
                )
            except StageResultValidationError:
                _raise_error(ReturnSessionPersistenceErrorCode.INVALID_INPUT)
            if (
                fulfillment.return_request_outcome is not return_request.outcome
                or fulfillment.return_request_context_digest
                != current.session.return_request_context.payload_digest
                or fulfillment.request_reference != return_request.request_reference
                or fulfillment.return_reference != return_request.return_reference
            ):
                _raise_error(ReturnSessionPersistenceErrorCode.INVALID_INPUT)
            context_update["fulfillment_tracking_context"] = transition.context_snapshot
        elif transition.completed_stage is WorkflowStage.BAY_ASSIGNMENT:
            if (
                current.session.bay_staging_context is not None
                or current.session.fulfillment_tracking_context is None
                or transition.context_snapshot is None
            ):
                _raise_error(ReturnSessionPersistenceErrorCode.STALE_STAGE)
            try:
                fulfillment = fulfillment_tracking_result_from_binding(
                    StageContextBinding(
                        completed_stage=WorkflowStage.FULFILLMENT_TRACKING,
                        schema_version=current.session.fulfillment_tracking_context.schema_version,
                        payload_json=current.session.fulfillment_tracking_context.payload_json,
                        payload_digest=current.session.fulfillment_tracking_context.payload_digest,
                    )
                )
                assignment = bay_assignment_result_from_binding(
                    StageContextBinding(
                        completed_stage=WorkflowStage.BAY_ASSIGNMENT,
                        schema_version=transition.context_snapshot.schema_version,
                        payload_json=transition.context_snapshot.payload_json,
                        payload_digest=transition.context_snapshot.payload_digest,
                    )
                )
            except StageResultValidationError:
                _raise_error(ReturnSessionPersistenceErrorCode.INVALID_INPUT)
            if (
                assignment.fulfillment_status is not fulfillment.status
                or assignment.fulfillment_context_digest
                != current.session.fulfillment_tracking_context.payload_digest
                or assignment.request_reference != fulfillment.request_reference
                or assignment.return_reference != fulfillment.return_reference
            ):
                _raise_error(ReturnSessionPersistenceErrorCode.INVALID_INPUT)
            context_update["bay_staging_context"] = transition.context_snapshot
        elif transition.completed_stage is WorkflowStage.FEEDBACK_LEARNING:
            if (
                current.session.learning_feedback_context is not None
                or current.session.bay_staging_context is None
                or transition.context_snapshot is None
            ):
                _raise_error(ReturnSessionPersistenceErrorCode.STALE_STAGE)
            try:
                assignment = bay_assignment_result_from_binding(
                    StageContextBinding(
                        completed_stage=WorkflowStage.BAY_ASSIGNMENT,
                        schema_version=current.session.bay_staging_context.schema_version,
                        payload_json=current.session.bay_staging_context.payload_json,
                        payload_digest=current.session.bay_staging_context.payload_digest,
                    )
                )
                feedback = feedback_learning_result_from_binding(
                    StageContextBinding(
                        completed_stage=WorkflowStage.FEEDBACK_LEARNING,
                        schema_version=transition.context_snapshot.schema_version,
                        payload_json=transition.context_snapshot.payload_json,
                        payload_digest=transition.context_snapshot.payload_digest,
                    )
                )
            except StageResultValidationError:
                _raise_error(ReturnSessionPersistenceErrorCode.INVALID_INPUT)
            if (
                feedback.bay_assignment_status is not assignment.status
                or feedback.bay_assignment_context_digest
                != current.session.bay_staging_context.payload_digest
                or feedback.request_reference != assignment.request_reference
                or feedback.return_reference != assignment.return_reference
                or feedback.warehouse_reference != assignment.warehouse_reference
                or feedback.bay_reference != assignment.bay_reference
            ):
                _raise_error(ReturnSessionPersistenceErrorCode.INVALID_INPUT)
            context_update["learning_feedback_context"] = transition.context_snapshot
        next_session = ReturnSession.model_validate(
            {
                **current.session.model_dump(mode="python"),
                **context_update,
                "current_stage": transition.resulting_stage,
                "status": next_status,
                "updated_at": transition.updated_at,
                "completed_at": completed_at,
            }
        )
        command = AppliedSessionCommand(
            command_id=transition.command_id,
            completed_stage=transition.completed_stage,
            resulting_stage=transition.resulting_stage,
            resulting_revision=current.revision + 1,
        )
        commands = (*current.applied_commands, command)
        digest_payload = {
            "schema_version": _SCHEMA_VERSION,
            "document_id": current.document_id,
            "revision": current.revision + 1,
            "session": next_session.model_dump(mode="json"),
            "applied_commands": [item.model_dump(mode="json") for item in commands],
        }
        next_document = ReturnSessionDocument.model_validate(
            {
                **digest_payload,
                "session": next_session,
                "applied_commands": commands,
                "state_digest": _state_digest(digest_payload),
            }
        )
        if (
            transition.audit_event.session_id != session_id
            or transition.outbox_event.session_id != session_id
            or transition.audit_event.occurred_at != transition.updated_at
            or transition.outbox_event.event_type != "RETURN_SESSION_STAGE_TRANSITIONED"
            or transition.outbox_event.status != next_status
            or transition.outbox_event.occurred_at != transition.updated_at
            or (
                transition.agent_decision is not None
                and (
                    transition.agent_decision.session_id != session_id
                    or transition.agent_decision.created_at != transition.updated_at
                )
            )
        ):
            _raise_error(ReturnSessionPersistenceErrorCode.INVALID_INPUT)
        try:
            async with asyncio.timeout(self._operation_timeout_seconds):
                stored, changed = await self._port.transition_bundle(
                    document_id=current.document_id,
                    expected_revision=current.revision,
                    completed_stage=transition.completed_stage.value,
                    command_id=str(transition.command_id),
                    session=next_document.to_mongo_document(),
                    audit=_mongo_document(
                        transition.audit_event, transition.audit_event.audit_event_id
                    ),
                    outbox=_mongo_document(
                        transition.outbox_event, transition.outbox_event.event_id
                    ),
                    decision=(
                        None
                        if transition.agent_decision is None
                        else _mongo_document(
                            transition.agent_decision,
                            transition.agent_decision.decision_id,
                        )
                    ),
                )
        except asyncio.CancelledError:
            raise
        except (AutoReconnect, NetworkTimeout, TimeoutError) as error:
            raise ReturnSessionPersistenceError(
                ReturnSessionPersistenceErrorCode.WRITE_OUTCOME_UNKNOWN
            ) from error
        except ExecutionTimeout as error:
            raise ReturnSessionPersistenceError(
                _write_error_code(error, ReturnSessionPersistenceErrorCode.TIMEOUT)
            ) from error
        except DuplicateKeyError as error:
            raise ReturnSessionPersistenceError(
                ReturnSessionPersistenceErrorCode.COMMAND_CONFLICT
            ) from error
        except OperationFailure as error:
            raise ReturnSessionPersistenceError(
                _write_error_code(error, ReturnSessionPersistenceErrorCode.WRITE_FAILED)
            ) from error
        except PyMongoError as error:
            raise ReturnSessionPersistenceError(
                _write_error_code(error, ReturnSessionPersistenceErrorCode.WRITE_FAILED)
            ) from error
        checked = ReturnSessionDocument.from_mongo_document(stored)
        if not changed:
            for applied in checked.applied_commands:
                if applied.command_id == transition.command_id:
                    if (
                        applied.completed_stage is transition.completed_stage
                        and applied.resulting_stage is transition.resulting_stage
                    ):
                        return ReturnSessionPersistenceReceipt(
                            status=ReturnSessionPersistenceStatus.ALREADY_APPLIED,
                            session_id=session_id,
                            revision=checked.revision,
                            current_stage=checked.session.current_stage,
                            state_digest=checked.state_digest,
                        )
                    _raise_error(ReturnSessionPersistenceErrorCode.COMMAND_CONFLICT)
            _raise_error(ReturnSessionPersistenceErrorCode.STALE_STAGE)
        if checked != next_document:
            _raise_error(ReturnSessionPersistenceErrorCode.IMMUTABLE_CONFLICT)
        return ReturnSessionPersistenceReceipt(
            status=ReturnSessionPersistenceStatus.TRANSITIONED,
            session_id=session_id,
            revision=checked.revision,
            current_stage=checked.session.current_stage,
            state_digest=checked.state_digest,
        )

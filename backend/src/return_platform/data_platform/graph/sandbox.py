"""Sandbox-only Customer graph validation orchestration."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, Never, Protocol, Self
from uuid import UUID

from pydantic import ValidationError, model_validator
from pydantic_core import PydanticCustomError

from return_platform.canonical import (
    CanonicalBaseModel,
    CanonicalIdentifier,
    GraphProjectionStatus,
    Sha256Digest,
    UtcDateTime,
    VersionReference,
)
from return_platform.data_platform.graph.commands import (
    CustomerNeo4jCommandBatch,
    Neo4jCommandBuildError,
    build_customer_neo4j_commands,
)
from return_platform.data_platform.graph.readback import (
    CustomerGraphIdempotencyEvidence,
    CustomerGraphReadbackEvidence,
    CustomerGraphReadbackValidator,
    assert_customer_graph_idempotency,
)
from return_platform.data_platform.graph.writer import CustomerNeo4jWriter
from return_platform.data_platform.mapping import (
    GraphMaterializationError,
    MappingCompilationError,
    MappingConfigurationLoadError,
    MappingExecutionPlan,
    NormalizationExecutionError,
    SourceDocumentEvidence,
    build_customer_account_canonical_model_registry,
    build_customer_account_handler_registry,
    compile_customer_profile_mapping,
    load_data_platform_mapping_configuration,
    materialize_customer_graph_projection,
    normalize_customer_source_document,
)
from return_platform.shared.governance import (
    AllowedOperation,
    AssetCatalog,
    AssetCatalogEntry,
    DataStoreType,
    ObjectKind,
    OwnershipClass,
)

__all__ = [
    "CustomerGraphSandboxError",
    "CustomerGraphSandboxErrorCode",
    "CustomerGraphSandboxExecutionEvidence",
    "CustomerGraphSandboxExecutionPort",
    "CustomerGraphSandboxExecutor",
    "CustomerGraphSandboxReport",
    "CustomerGraphSandboxService",
    "build_customer_graph_sandbox_catalog",
]

SANDBOX_VALIDATOR_VERSION: Final = "1.0"
_SANDBOX_EVIDENCE_DOMAIN: Final = "return-platform:customer-graph-sandbox:v1"
_SANDBOX_SOURCE_ASSET_ID: Final = "source.mongodb.customer_outbound_cdm"
_SANDBOX_DATABASE: Final = "eventMessages"
_SANDBOX_COLLECTION: Final = "customerOutboundCDM"


class CustomerGraphSandboxErrorCode(StrEnum):
    """Stable safe sandbox validation failure codes."""

    INVALID_INPUT = "INVALID_INPUT"
    CONFIGURATION_FAILED = "CONFIGURATION_FAILED"
    NORMALIZATION_REJECTED = "NORMALIZATION_REJECTED"
    MATERIALIZATION_REJECTED = "MATERIALIZATION_REJECTED"
    EXECUTION_EVIDENCE_INVALID = "EXECUTION_EVIDENCE_INVALID"


_SAFE_MESSAGES: Final = {
    CustomerGraphSandboxErrorCode.INVALID_INPUT: ("Customer graph sandbox inputs are invalid."),
    CustomerGraphSandboxErrorCode.CONFIGURATION_FAILED: (
        "Customer graph sandbox mapping configuration is invalid."
    ),
    CustomerGraphSandboxErrorCode.NORMALIZATION_REJECTED: (
        "The controlled Customer source document was rejected by normalization."
    ),
    CustomerGraphSandboxErrorCode.MATERIALIZATION_REJECTED: (
        "The controlled Customer graph materialization was rejected."
    ),
    CustomerGraphSandboxErrorCode.EXECUTION_EVIDENCE_INVALID: (
        "Customer graph sandbox execution evidence is invalid."
    ),
}


class CustomerGraphSandboxError(RuntimeError):
    """Safe sandbox validation error with a stable public code."""

    def __init__(self, code: CustomerGraphSandboxErrorCode) -> None:
        """Initialize one safe sandbox error."""
        self.code = code
        self.safe_message = _SAFE_MESSAGES[code]
        super().__init__(self.safe_message)


def _raise_error(code: CustomerGraphSandboxErrorCode) -> Never:
    """Raise one safe sandbox validation error."""
    raise CustomerGraphSandboxError(code)


def _raise_model_error(error_type: str, message: str) -> Never:
    """Raise one stable Pydantic contract error."""
    raise PydanticCustomError(error_type, message)


def _canonical_json(payload: object) -> bytes:
    """Serialize one sandbox evidence payload deterministically."""
    return json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _utc_json(value: datetime) -> str:
    """Serialize one UTC datetime exactly as Pydantic JSON mode does."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sha256(payload: object) -> str:
    """Hash one domain-separated sandbox evidence payload."""
    digest = hashlib.sha256()
    domain = _SANDBOX_EVIDENCE_DOMAIN.encode("ascii")
    digest.update(len(domain).to_bytes(4, "big"))
    digest.update(domain)
    encoded = _canonical_json(payload)
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)
    return digest.hexdigest()


class CustomerGraphSandboxExecutionEvidence(CanonicalBaseModel):
    """Complete two-run write/read-back/idempotency execution evidence."""

    validator_version: VersionReference
    command_batch_digest: Sha256Digest
    execution_plan_digest: Sha256Digest
    schema_evidence_digest: Sha256Digest
    first_write_evidence_digest: Sha256Digest
    second_write_evidence_digest: Sha256Digest
    first_readback: CustomerGraphReadbackEvidence
    second_readback: CustomerGraphReadbackEvidence
    idempotency: CustomerGraphIdempotencyEvidence
    evidence_digest: Sha256Digest

    @classmethod
    def create(
        cls,
        *,
        batch: CustomerNeo4jCommandBatch,
        schema_evidence_digest: str,
        first_write_evidence_digest: str,
        second_write_evidence_digest: str,
        first_readback: CustomerGraphReadbackEvidence,
        second_readback: CustomerGraphReadbackEvidence,
        idempotency: CustomerGraphIdempotencyEvidence,
    ) -> Self:
        """Create validated digest-bound two-run execution evidence."""
        digest_payload = _execution_creation_payload(
            batch=batch,
            schema_evidence_digest=schema_evidence_digest,
            first_write_evidence_digest=first_write_evidence_digest,
            second_write_evidence_digest=second_write_evidence_digest,
            first_readback=first_readback,
            second_readback=second_readback,
            idempotency=idempotency,
        )
        model_payload: dict[str, object] = {
            "validator_version": SANDBOX_VALIDATOR_VERSION,
            "command_batch_digest": batch.command_batch_digest,
            "execution_plan_digest": batch.execution_plan_digest,
            "schema_evidence_digest": schema_evidence_digest,
            "first_write_evidence_digest": first_write_evidence_digest,
            "second_write_evidence_digest": second_write_evidence_digest,
            "first_readback": first_readback,
            "second_readback": second_readback,
            "idempotency": idempotency,
        }
        return cls.model_validate(
            {
                **model_payload,
                "evidence_digest": _sha256(digest_payload),
            }
        )

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        """Require all nested evidence to bind to one exact command batch."""
        if (
            self.first_readback.command_batch_digest != self.command_batch_digest
            or self.second_readback.command_batch_digest != self.command_batch_digest
            or self.idempotency.command_batch_digest != self.command_batch_digest
            or self.first_readback.execution_plan_digest != self.execution_plan_digest
            or self.second_readback.execution_plan_digest != self.execution_plan_digest
            or self.idempotency.execution_plan_digest != self.execution_plan_digest
            or self.first_readback.write_evidence_digest != self.first_write_evidence_digest
            or self.second_readback.write_evidence_digest != self.second_write_evidence_digest
            or self.idempotency.first_write_evidence_digest != self.first_write_evidence_digest
            or self.idempotency.second_write_evidence_digest != self.second_write_evidence_digest
            or not self.idempotency.idempotent
        ):
            _raise_model_error(
                "customer_sandbox_nested_evidence_mismatch",
                "sandbox nested evidence must bind to one idempotent batch",
            )
        if self.evidence_digest != _execution_evidence_digest(self):
            _raise_model_error(
                "customer_sandbox_evidence_digest_mismatch",
                "sandbox execution evidence digest does not match its contents",
            )
        return self


class CustomerGraphSandboxReport(CanonicalBaseModel):
    """Successful sandbox process report suitable for JSON evidence output."""

    validator_version: VersionReference
    evidence_classification: Literal["SANDBOX_VALIDATED"]
    process_exit_code: Literal[0]
    executed_at: UtcDateTime
    source_document_id: CanonicalIdentifier
    source_hash: Sha256Digest
    configuration_digest: Sha256Digest
    execution_plan_digest: Sha256Digest
    command_batch_digest: Sha256Digest
    sync_run_id: UUID
    expected_customer_count: int
    expected_customer_account_count: int
    expected_relationship_count: int
    execution: CustomerGraphSandboxExecutionEvidence
    report_digest: Sha256Digest

    @classmethod
    def create(
        cls,
        *,
        executed_at: datetime,
        source_document_id: str,
        source_hash: str,
        configuration_digest: str,
        execution_plan_digest: str,
        command_batch_digest: str,
        sync_run_id: UUID,
        expected_customer_account_count: int,
        execution: CustomerGraphSandboxExecutionEvidence,
    ) -> Self:
        """Create one successful digest-bound sandbox report."""
        digest_payload: dict[str, object] = {
            "validator_version": SANDBOX_VALIDATOR_VERSION,
            "evidence_classification": "SANDBOX_VALIDATED",
            "process_exit_code": 0,
            "executed_at": _utc_json(executed_at),
            "source_document_id": source_document_id,
            "source_hash": source_hash,
            "configuration_digest": configuration_digest,
            "execution_plan_digest": execution_plan_digest,
            "command_batch_digest": command_batch_digest,
            "sync_run_id": str(sync_run_id),
            "expected_customer_count": 1,
            "expected_customer_account_count": expected_customer_account_count,
            "expected_relationship_count": expected_customer_account_count,
            "execution": execution.model_dump(mode="json"),
        }
        model_payload: dict[str, object] = {
            "validator_version": SANDBOX_VALIDATOR_VERSION,
            "evidence_classification": "SANDBOX_VALIDATED",
            "process_exit_code": 0,
            "executed_at": executed_at,
            "source_document_id": source_document_id,
            "source_hash": source_hash,
            "configuration_digest": configuration_digest,
            "execution_plan_digest": execution_plan_digest,
            "command_batch_digest": command_batch_digest,
            "sync_run_id": sync_run_id,
            "expected_customer_count": 1,
            "expected_customer_account_count": expected_customer_account_count,
            "expected_relationship_count": expected_customer_account_count,
            "execution": execution,
        }
        return cls.model_validate(
            {
                **model_payload,
                "report_digest": _sha256(digest_payload),
            }
        )

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        """Require exact successful counts and digest-bound execution evidence."""
        if (
            self.expected_customer_count != 1
            or self.expected_customer_account_count < 1
            or self.expected_relationship_count != self.expected_customer_account_count
            or self.execution_plan_digest != self.execution.execution_plan_digest
            or self.command_batch_digest != self.execution.command_batch_digest
            or self.execution.idempotency.expected_customer_count != self.expected_customer_count
            or self.execution.idempotency.expected_customer_account_count
            != self.expected_customer_account_count
            or self.execution.idempotency.expected_relationship_count
            != self.expected_relationship_count
        ):
            _raise_model_error(
                "customer_sandbox_report_counts_invalid",
                "sandbox report counts or execution bindings are invalid",
            )
        if self.report_digest != _report_digest(self):
            _raise_model_error(
                "customer_sandbox_report_digest_mismatch",
                "sandbox report digest does not match its contents",
            )
        return self


class CustomerGraphSandboxExecutionPort(Protocol):
    """Port for executing one deterministic command batch twice."""

    async def execute_twice(
        self,
        *,
        batch: CustomerNeo4jCommandBatch,
        database: str,
        transaction_timeout_seconds: float,
        operation_timeout_seconds: float,
    ) -> CustomerGraphSandboxExecutionEvidence:
        """Write, read back, repeat, and prove exact idempotency."""
        ...


def _execution_unsigned_payload(
    evidence: CustomerGraphSandboxExecutionEvidence,
) -> dict[str, object]:
    """Build an unsigned JSON-compatible execution evidence payload."""
    payload = evidence.model_dump(mode="json", exclude={"evidence_digest"})
    return {str(key): value for key, value in payload.items()}


def _execution_evidence_digest(
    evidence: CustomerGraphSandboxExecutionEvidence,
) -> str:
    """Calculate execution evidence integrity."""
    return _sha256(_execution_unsigned_payload(evidence))


def _report_unsigned_payload(
    report: CustomerGraphSandboxReport,
) -> dict[str, object]:
    """Build an unsigned JSON-compatible report payload."""
    payload = report.model_dump(mode="json", exclude={"report_digest"})
    return {str(key): value for key, value in payload.items()}


def _report_digest(report: CustomerGraphSandboxReport) -> str:
    """Calculate report integrity."""
    return _sha256(_report_unsigned_payload(report))


def _execution_creation_payload(
    *,
    batch: CustomerNeo4jCommandBatch,
    schema_evidence_digest: str,
    first_write_evidence_digest: str,
    second_write_evidence_digest: str,
    first_readback: CustomerGraphReadbackEvidence,
    second_readback: CustomerGraphReadbackEvidence,
    idempotency: CustomerGraphIdempotencyEvidence,
) -> dict[str, object]:
    """Build JSON-compatible execution evidence before its own digest."""
    return {
        "validator_version": SANDBOX_VALIDATOR_VERSION,
        "command_batch_digest": batch.command_batch_digest,
        "execution_plan_digest": batch.execution_plan_digest,
        "schema_evidence_digest": schema_evidence_digest,
        "first_write_evidence_digest": first_write_evidence_digest,
        "second_write_evidence_digest": second_write_evidence_digest,
        "first_readback": first_readback.model_dump(mode="json"),
        "second_readback": second_readback.model_dump(mode="json"),
        "idempotency": idempotency.model_dump(mode="json"),
    }


class CustomerGraphSandboxExecutor:
    """Concrete executor using the existing writer and fixed read-back validator."""

    def __init__(
        self,
        writer: CustomerNeo4jWriter,
        readback: CustomerGraphReadbackValidator,
    ) -> None:
        """Store injected graph write and read components."""
        self._writer = writer
        self._readback = readback

    async def execute_twice(
        self,
        *,
        batch: CustomerNeo4jCommandBatch,
        database: str,
        transaction_timeout_seconds: float,
        operation_timeout_seconds: float,
    ) -> CustomerGraphSandboxExecutionEvidence:
        """Execute the same immutable batch twice and validate exact graph state."""
        schema_evidence = await self._writer.prepare_schema(
            batch=batch,
            database=database,
            transaction_timeout_seconds=transaction_timeout_seconds,
            operation_timeout_seconds=operation_timeout_seconds,
        )
        first_write = await self._writer.write_data(
            batch=batch,
            schema_evidence=schema_evidence,
            database=database,
            transaction_timeout_seconds=transaction_timeout_seconds,
            operation_timeout_seconds=operation_timeout_seconds,
        )
        first_readback = await self._readback.read_back(
            batch=batch,
            write_evidence=first_write,
            database=database,
            transaction_timeout_seconds=transaction_timeout_seconds,
            operation_timeout_seconds=operation_timeout_seconds,
        )
        second_write = await self._writer.write_data(
            batch=batch,
            schema_evidence=schema_evidence,
            database=database,
            transaction_timeout_seconds=transaction_timeout_seconds,
            operation_timeout_seconds=operation_timeout_seconds,
        )
        second_readback = await self._readback.read_back(
            batch=batch,
            write_evidence=second_write,
            database=database,
            transaction_timeout_seconds=transaction_timeout_seconds,
            operation_timeout_seconds=operation_timeout_seconds,
        )
        idempotency = assert_customer_graph_idempotency(
            first_readback,
            second_readback,
            first_write_evidence_digest=first_write.evidence_digest,
            second_write_evidence_digest=second_write.evidence_digest,
        )
        return CustomerGraphSandboxExecutionEvidence.create(
            batch=batch,
            schema_evidence_digest=schema_evidence.evidence_digest,
            first_write_evidence_digest=first_write.evidence_digest,
            second_write_evidence_digest=second_write.evidence_digest,
            first_readback=first_readback,
            second_readback=second_readback,
            idempotency=idempotency,
        )


def build_customer_graph_sandbox_catalog() -> AssetCatalog:
    """Build an explicit in-memory sandbox catalog without production mutation."""
    return AssetCatalog(
        version="1.0",
        assets=(
            AssetCatalogEntry(
                asset_id=_SANDBOX_SOURCE_ASSET_ID,
                store=DataStoreType.MONGODB,
                database=_SANDBOX_DATABASE,
                namespace=None,
                object_name=_SANDBOX_COLLECTION,
                object_kind=ObjectKind.COLLECTION,
                ownership=OwnershipClass.SOURCE_SYSTEM,
                authoritative=True,
                allowed_operations=(AllowedOperation.READ,),
            ),
        ),
    )


def _compile_sandbox_plan(config_dir: Path) -> MappingExecutionPlan:
    """Load mappings and compile only against the explicit sandbox catalog."""
    try:
        loaded = load_data_platform_mapping_configuration(config_dir)
        return compile_customer_profile_mapping(
            loaded,
            build_customer_graph_sandbox_catalog(),
            build_customer_account_handler_registry(),
            build_customer_account_canonical_model_registry(),
        )
    except (
        OSError,
        MappingCompilationError,
        MappingConfigurationLoadError,
        ValidationError,
    ) as error:
        raise CustomerGraphSandboxError(
            CustomerGraphSandboxErrorCode.CONFIGURATION_FAILED
        ) from error


class CustomerGraphSandboxService:
    """Pure-to-I/O sandbox pipeline for one controlled Customer document."""

    def __init__(self, execution_port: CustomerGraphSandboxExecutionPort) -> None:
        """Store the injected two-run graph execution boundary."""
        self._execution_port = execution_port

    async def validate(
        self,
        *,
        config_dir: Path,
        source_document: dict[str, object],
        source_evidence: SourceDocumentEvidence,
        source_hash: str,
        sync_run_id: UUID,
        graph_synced_at: datetime,
        database: str,
        transaction_timeout_seconds: float,
        operation_timeout_seconds: float,
    ) -> CustomerGraphSandboxReport:
        """Run the controlled mapping-to-idempotency pipeline once."""
        if (
            not isinstance(config_dir, Path)
            or not isinstance(source_document, dict)
            or not isinstance(source_evidence, SourceDocumentEvidence)
            or not isinstance(sync_run_id, UUID)
            or not isinstance(graph_synced_at, datetime)
            or graph_synced_at.tzinfo is None
            or graph_synced_at.utcoffset() is None
            or not isinstance(source_hash, str)
            or len(source_hash) != 64
            or any(character not in "0123456789abcdef" for character in source_hash)
            or source_hash != source_evidence.source_hash
        ):
            _raise_error(CustomerGraphSandboxErrorCode.INVALID_INPUT)
        plan = _compile_sandbox_plan(config_dir)
        try:
            normalization = normalize_customer_source_document(
                plan,
                source_evidence,
                source_document,
            )
        except NormalizationExecutionError as error:
            raise CustomerGraphSandboxError(
                CustomerGraphSandboxErrorCode.NORMALIZATION_REJECTED
            ) from error
        if (
            normalization.customer is None
            or not normalization.customer_accounts
            or normalization.rejections
        ):
            _raise_error(CustomerGraphSandboxErrorCode.NORMALIZATION_REJECTED)
        try:
            materialization = materialize_customer_graph_projection(
                plan,
                normalization,
                sync_run_id=sync_run_id,
                graph_synced_at=graph_synced_at.astimezone(UTC),
            )
        except GraphMaterializationError as error:
            raise CustomerGraphSandboxError(
                CustomerGraphSandboxErrorCode.MATERIALIZATION_REJECTED
            ) from error
        if (
            materialization.customer_node is None
            or not materialization.customer_account_nodes
            or materialization.rejected_count != 0
            or materialization.node_count != 1 + len(normalization.customer_accounts)
            or materialization.relationship_count != len(normalization.customer_accounts)
            or any(
                evidence.projection_status is not GraphProjectionStatus.PROJECTED
                for evidence in materialization.projection_evidence
            )
        ):
            _raise_error(CustomerGraphSandboxErrorCode.MATERIALIZATION_REJECTED)
        try:
            batch = build_customer_neo4j_commands(materialization)
        except Neo4jCommandBuildError as error:
            raise CustomerGraphSandboxError(
                CustomerGraphSandboxErrorCode.MATERIALIZATION_REJECTED
            ) from error
        execution = await self._execution_port.execute_twice(
            batch=batch,
            database=database,
            transaction_timeout_seconds=transaction_timeout_seconds,
            operation_timeout_seconds=operation_timeout_seconds,
        )
        if not isinstance(execution, CustomerGraphSandboxExecutionEvidence):
            _raise_error(CustomerGraphSandboxErrorCode.EXECUTION_EVIDENCE_INVALID)
        try:
            checked_execution = CustomerGraphSandboxExecutionEvidence.model_validate(
                execution.model_dump(mode="python")
            )
        except ValidationError as error:
            raise CustomerGraphSandboxError(
                CustomerGraphSandboxErrorCode.EXECUTION_EVIDENCE_INVALID
            ) from error
        if (
            checked_execution.command_batch_digest != batch.command_batch_digest
            or checked_execution.execution_plan_digest != plan.execution_plan_digest
        ):
            _raise_error(CustomerGraphSandboxErrorCode.EXECUTION_EVIDENCE_INVALID)
        return CustomerGraphSandboxReport.create(
            executed_at=datetime.now(UTC),
            source_document_id=source_evidence.source_document_id,
            source_hash=source_hash,
            configuration_digest=plan.configuration_digest,
            execution_plan_digest=plan.execution_plan_digest,
            command_batch_digest=batch.command_batch_digest,
            sync_run_id=sync_run_id,
            expected_customer_account_count=len(materialization.customer_account_nodes),
            execution=checked_execution,
        )

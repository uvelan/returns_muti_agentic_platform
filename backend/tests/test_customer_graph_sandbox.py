"""Adversarial tests for sandbox-only Customer graph validation orchestration."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

import pytest
from pydantic import ValidationError
from pymongo import IndexModel
from pymongo.errors import DuplicateKeyError

from return_platform.data_platform.graph import sandbox_runner
from return_platform.data_platform.graph.commands import (
    CustomerNeo4jCommandBatch,
    Neo4jNodeUpsertCommand,
)
from return_platform.data_platform.graph.evidence_query import (
    CustomerGraphEvidenceQueryError,
    CustomerGraphEvidenceQueryErrorCode,
    CustomerGraphEvidenceQueryRepository,
    decode_customer_graph_evidence_cursor,
)
from return_platform.data_platform.graph.evidence_repository import (
    CustomerGraphEvidenceDocument,
    CustomerGraphEvidencePersistenceError,
    CustomerGraphEvidencePersistenceErrorCode,
    CustomerGraphEvidencePersistenceStatus,
    CustomerGraphEvidenceRepository,
)
from return_platform.data_platform.graph.readback import (
    CustomerGraphReadbackEvidence,
    assert_customer_graph_idempotency,
    validate_customer_graph_snapshot_records,
)
from return_platform.data_platform.graph.sandbox import (
    CustomerGraphSandboxError,
    CustomerGraphSandboxErrorCode,
    CustomerGraphSandboxExecutionEvidence,
    CustomerGraphSandboxExecutionPort,
    CustomerGraphSandboxReport,
    CustomerGraphSandboxService,
    build_customer_graph_sandbox_catalog,
)
from return_platform.data_platform.mapping import SourceDocumentEvidence
from return_platform.shared.governance import (
    AllowedOperation,
    DataStoreType,
    ObjectKind,
    OwnershipClass,
)

_CONFIG_DIR = Path(__file__).parents[1] / "config" / "data_platform"
_SYNC_RUN_ID = UUID("12345678-1234-4678-9234-567812345678")
_SOURCE_UPDATED_AT = datetime(2026, 7, 22, 4, 0, tzinfo=UTC)
_GRAPH_SYNCED_AT = datetime(2026, 7, 22, 5, 0, tzinfo=UTC)
_SOURCE_HASH = "a" * 64
_FIRST_WRITE_DIGEST = "b" * 64
_SECOND_WRITE_DIGEST = "c" * 64
_SCHEMA_DIGEST = "d" * 64
_DATABASE = "neo4j"
_EXPECTED_ACCOUNT_COUNT = 2
_EXPECTED_NODE_COUNT = 3
_EXPECTED_RELATIONSHIP_COUNT = 2


class _MutableSandboxReport(Protocol):
    report_digest: str


class _FakeExecutionPort(CustomerGraphSandboxExecutionPort):
    """Build valid deterministic execution evidence without graph I/O."""

    def __init__(self, *, corrupt: bool = False) -> None:
        self.corrupt = corrupt
        self.calls: list[tuple[CustomerNeo4jCommandBatch, str, float, float]] = []

    async def execute_twice(
        self,
        *,
        batch: CustomerNeo4jCommandBatch,
        database: str,
        transaction_timeout_seconds: float,
        operation_timeout_seconds: float,
    ) -> CustomerGraphSandboxExecutionEvidence:
        """Return exact two-run read-back evidence for the received batch."""
        self.calls.append(
            (
                batch,
                database,
                transaction_timeout_seconds,
                operation_timeout_seconds,
            )
        )
        customer_command, account_commands = _commands(batch)
        customer_record = _node_record(customer_command)
        account_records = tuple(_node_record(item) for item in account_commands)
        relationship_records = tuple(
            {
                "account_key": item.parameters.target_key,
                "match_count": 1,
                "source_key": item.parameters.source_key,
                "target_key": item.parameters.target_key,
            }
            for item in batch.relationship_commands
        )
        customer, accounts, relationships = validate_customer_graph_snapshot_records(
            batch=batch,
            customer_record=customer_record,
            account_records=account_records,
            relationship_records=relationship_records,
        )
        started_at = datetime(2026, 7, 22, 5, 1, tzinfo=UTC)
        first = CustomerGraphReadbackEvidence.create(
            command_batch_digest=batch.command_batch_digest,
            write_evidence_digest=_FIRST_WRITE_DIGEST,
            execution_plan_digest=batch.execution_plan_digest,
            sync_run_id=batch.sync_run_id,
            database=database,
            transaction_timeout_seconds=transaction_timeout_seconds,
            operation_timeout_seconds=operation_timeout_seconds,
            started_at=started_at,
            completed_at=started_at + timedelta(milliseconds=1),
            customer=customer,
            customer_accounts=accounts,
            has_account_relationships=relationships,
        )
        second = CustomerGraphReadbackEvidence.create(
            command_batch_digest=batch.command_batch_digest,
            write_evidence_digest=_SECOND_WRITE_DIGEST,
            execution_plan_digest=batch.execution_plan_digest,
            sync_run_id=batch.sync_run_id,
            database=database,
            transaction_timeout_seconds=transaction_timeout_seconds,
            operation_timeout_seconds=operation_timeout_seconds,
            started_at=started_at + timedelta(seconds=1),
            completed_at=started_at + timedelta(seconds=1, milliseconds=1),
            customer=customer,
            customer_accounts=accounts,
            has_account_relationships=relationships,
        )
        idempotency = assert_customer_graph_idempotency(
            first,
            second,
            first_write_evidence_digest=_FIRST_WRITE_DIGEST,
            second_write_evidence_digest=_SECOND_WRITE_DIGEST,
        )
        evidence = CustomerGraphSandboxExecutionEvidence.create(
            batch=batch,
            schema_evidence_digest=_SCHEMA_DIGEST,
            first_write_evidence_digest=_FIRST_WRITE_DIGEST,
            second_write_evidence_digest=_SECOND_WRITE_DIGEST,
            first_readback=first,
            second_readback=second,
            idempotency=idempotency,
        )
        if self.corrupt:
            return evidence.model_copy(update={"command_batch_digest": "f" * 64})
        return evidence


def _commands(
    batch: CustomerNeo4jCommandBatch,
) -> tuple[Neo4jNodeUpsertCommand, tuple[Neo4jNodeUpsertCommand, ...]]:
    """Resolve fixed Customer and CustomerAccount commands from one batch."""
    customer = tuple(
        item for item in batch.node_commands if item.node_mapping_id == "graph.customer.v1"
    )
    accounts = tuple(
        item for item in batch.node_commands if item.node_mapping_id == "graph.customer_account.v1"
    )
    assert len(customer) == 1
    return customer[0], accounts


def _neo4j_text(value: object) -> object:
    """Convert graph datetime parameters to fixed-query result text."""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return value


def _node_record(
    command: Neo4jNodeUpsertCommand,
) -> Mapping[str, object]:
    """Build a fixed-query result directly from approved command properties."""
    properties = command.parameters.properties.as_mapping()
    values = {
        name: _neo4j_text(properties[name])
        for name in (
            "canonical_key",
            "configuration_digest",
            "graph_synced_at",
            "identity_quality",
            "mapping_version",
            "source_asset",
            "source_database",
            "source_record_id",
            "source_system",
            "source_updated_at",
            "sync_run_id",
        )
    }
    values[command.key_property] = command.parameters.key
    values["match_count"] = 1
    return values


def _document() -> dict[str, object]:
    """Return one controlled Customer source document."""
    return {
        "partyId": "P100",
        "custAccts": [
            {"accountNumber": "202*C001"},
            {"accountNumber": "203*C002"},
        ],
    }


def _evidence(
    *,
    source_document_id: str = "P100",
    source_hash: str = _SOURCE_HASH,
) -> SourceDocumentEvidence:
    """Return exact controlled source evidence."""
    return SourceDocumentEvidence(
        source_document_id=source_document_id,
        source_updated_at=_SOURCE_UPDATED_AT,
        source_version="17",
        source_event_id="evt-100",
        source_hash=source_hash,
        observed_at=datetime(2026, 7, 22, 4, 1, tzinfo=UTC),
    )


async def _validate(
    port: _FakeExecutionPort,
    *,
    document: dict[str, object] | None = None,
    evidence: SourceDocumentEvidence | None = None,
    source_hash: str = _SOURCE_HASH,
) -> CustomerGraphSandboxReport:
    """Execute the service with deterministic controlled inputs."""
    return await CustomerGraphSandboxService(port).validate(
        config_dir=_CONFIG_DIR,
        source_document=document or _document(),
        source_evidence=evidence or _evidence(),
        source_hash=source_hash,
        sync_run_id=_SYNC_RUN_ID,
        graph_synced_at=_GRAPH_SYNCED_AT,
        database=_DATABASE,
        transaction_timeout_seconds=2.0,
        operation_timeout_seconds=3.0,
    )


def test_sandbox_catalog_is_explicit_read_only_and_not_production_catalog() -> None:
    """Compile against one in-memory source asset without catalog mutation."""
    catalog = build_customer_graph_sandbox_catalog()

    assert catalog.version == "1.0"
    assert len(catalog.assets) == 1
    asset = catalog.assets[0]
    assert asset.asset_id == "source.mongodb.customer_outbound_cdm"
    assert asset.store is DataStoreType.MONGODB
    assert asset.object_kind is ObjectKind.COLLECTION
    assert asset.ownership is OwnershipClass.SOURCE_SYSTEM
    assert asset.authoritative is True
    assert asset.allowed_operations == (AllowedOperation.READ,)


@pytest.mark.asyncio
async def test_runs_complete_controlled_pipeline_and_emits_success_report() -> None:
    """Compile, normalize, materialize, execute twice, and prove idempotency."""
    port = _FakeExecutionPort()

    report = await _validate(port)

    assert report.evidence_classification == "SANDBOX_VALIDATED"
    assert report.process_exit_code == 0
    assert report.expected_customer_count == 1
    assert report.expected_customer_account_count == _EXPECTED_ACCOUNT_COUNT
    assert report.expected_relationship_count == _EXPECTED_RELATIONSHIP_COUNT
    assert report.execution.idempotency.idempotent is True
    assert len(port.calls) == 1
    batch = port.calls[0][0]
    assert len(batch.node_commands) == _EXPECTED_NODE_COUNT
    assert len(batch.relationship_commands) == _EXPECTED_RELATIONSHIP_COUNT


@pytest.mark.asyncio
async def test_rejects_source_hash_disagreement_before_execution() -> None:
    """Prevent detached source bytes from borrowing unrelated source evidence."""
    port = _FakeExecutionPort()

    with pytest.raises(CustomerGraphSandboxError) as exc_info:
        await _validate(port, source_hash="f" * 64)

    assert exc_info.value.code is CustomerGraphSandboxErrorCode.INVALID_INPUT
    assert port.calls == []


@pytest.mark.asyncio
async def test_rejects_source_identity_mismatch_before_graph_access() -> None:
    """Fail closed when party identity differs from source-document evidence."""
    port = _FakeExecutionPort()

    with pytest.raises(CustomerGraphSandboxError) as exc_info:
        await _validate(port, evidence=_evidence(source_document_id="OTHER"))

    assert exc_info.value.code is (CustomerGraphSandboxErrorCode.NORMALIZATION_REJECTED)
    assert port.calls == []


@pytest.mark.asyncio
async def test_rejects_customer_without_accounts_for_this_validation_slice() -> None:
    """Require at least one CustomerAccount and HAS_ACCOUNT edge in this runner."""
    port = _FakeExecutionPort()

    with pytest.raises(CustomerGraphSandboxError) as exc_info:
        await _validate(port, document={"partyId": "P100", "custAccts": []})

    assert exc_info.value.code is (CustomerGraphSandboxErrorCode.NORMALIZATION_REJECTED)
    assert port.calls == []


@pytest.mark.asyncio
async def test_revalidates_execution_evidence_returned_by_the_io_boundary() -> None:
    """Reject a fake executor that returns digest-detached batch evidence."""
    port = _FakeExecutionPort(corrupt=True)

    with pytest.raises(CustomerGraphSandboxError) as exc_info:
        await _validate(port)

    assert exc_info.value.code is (CustomerGraphSandboxErrorCode.EXECUTION_EVIDENCE_INVALID)
    assert len(port.calls) == 1


@pytest.mark.asyncio
async def test_report_is_frozen_and_digest_bound() -> None:
    """Reject mutation or replacement of successful sandbox evidence."""
    report = await _validate(_FakeExecutionPort())
    mutable = cast(_MutableSandboxReport, report)

    with pytest.raises(ValidationError):
        mutable.report_digest = "f" * 64
    with pytest.raises(ValidationError):
        CustomerGraphSandboxReport.model_validate(
            {**report.model_dump(mode="python"), "report_digest": "f" * 64}
        )


def test_runner_rejects_duplicate_json_keys_nonfinite_values_and_arrays(
    tmp_path: Path,
) -> None:
    """Reject ambiguous or non-standard controlled source JSON."""
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"partyId":"P100","partyId":"P200"}', encoding="utf-8")
    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"partyId":NaN}', encoding="utf-8")
    array = tmp_path / "array.json"
    array.write_text("[]", encoding="utf-8")

    for path in (duplicate, nonfinite, array):
        with pytest.raises(sandbox_runner.SandboxRunnerError) as exc_info:
            sandbox_runner.load_customer_graph_source_document(path)
        assert exc_info.value.code is (sandbox_runner.SandboxRunnerErrorCode.SOURCE_JSON_INVALID)


def test_runner_rejects_symlink_and_oversized_source_files(tmp_path: Path) -> None:
    """Prevent path substitution and unbounded source-document loading."""
    target = tmp_path / "target.json"
    target.write_text(json.dumps(_document()), encoding="utf-8")
    
    paths_to_test = []
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
        paths_to_test.append(link)
    except OSError as e:
        if getattr(e, "winerror", None) != 1314:
            raise

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{" + b" " * 1_048_576 + b"}")
    paths_to_test.append(oversized)

    for path in paths_to_test:
        with pytest.raises(sandbox_runner.SandboxRunnerError) as exc_info:
            sandbox_runner.load_customer_graph_source_document(path)
        assert exc_info.value.code is (sandbox_runner.SandboxRunnerErrorCode.SOURCE_FILE_INVALID)


def test_runner_hashes_exact_source_bytes(tmp_path: Path) -> None:
    """Bind source evidence to exact bytes rather than normalized JSON."""
    source_file = tmp_path / "customer.json"
    raw = b'{"partyId":"P100","custAccts":[]}'
    source_file.write_bytes(raw)

    loaded = sandbox_runner.load_customer_graph_source_document(source_file)

    assert loaded.source_hash == hashlib.sha256(raw).hexdigest()
    assert loaded.document["partyId"] == "P100"


def test_sandbox_source_has_no_production_catalog_or_forbidden_components() -> None:
    """Enforce the declared stop boundary structurally."""
    source_path = Path(__file__).parents[1] / "src/return_platform/data_platform/graph/sandbox.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    source = source_path.read_text(encoding="utf-8")

    assert "return_platform.data_governance" not in imported_modules
    assert "temporalio" not in source
    assert "pymongo" not in source
    assert "SalesOrder" not in source
    assert "Package" not in source
    assert "PPLTracking" not in source
    assert "data_assets.yaml" not in source


@dataclass(frozen=True, slots=True)
class _FakeEvidenceUpdateResult:
    """Deterministic narrow update result used by repository tests."""

    upserted_id: object | None
    matched_count: int
    modified_count: int


class _FakeEvidenceCollection:
    """In-memory immutable collection boundary for repository contract tests."""

    def __init__(self) -> None:
        """Initialize empty collection state."""
        self.document: dict[str, object] | None = None
        self.indexes: tuple[IndexModel, ...] = ()
        self.update_calls = 0
        self.read_calls = 0
        self.block_update: asyncio.Event | None = None

    async def create_indexes(self, indexes: list[IndexModel]) -> list[str]:
        """Record the exact code-owned indexes."""
        await asyncio.sleep(0)
        self.indexes = tuple(indexes)
        return [str(index.document["name"]) for index in indexes]

    async def update_one(
        self,
        filter_document: Mapping[str, object],
        update_document: Mapping[str, object],
        /,
        *,
        upsert: bool,
    ) -> _FakeEvidenceUpdateResult:
        """Apply one $setOnInsert operation without mutation on replay."""
        self.update_calls += 1
        if self.block_update is not None:
            await self.block_update.wait()
        assert upsert is True
        raw_candidate = update_document.get("$setOnInsert")
        assert isinstance(raw_candidate, Mapping)
        candidate = {str(key): value for key, value in raw_candidate.items()}
        document_id = filter_document.get("_id")
        document_digest = filter_document.get("document_digest")
        if self.document is None:
            self.document = candidate
            return _FakeEvidenceUpdateResult(
                upserted_id=document_id,
                matched_count=0,
                modified_count=0,
            )
        if (
            self.document.get("_id") == document_id
            and self.document.get("document_digest") == document_digest
        ):
            return _FakeEvidenceUpdateResult(
                upserted_id=None,
                matched_count=1,
                modified_count=0,
            )
        raise DuplicateKeyError("immutable graph evidence conflict")

    async def find_one(
        self,
        filter_document: Mapping[str, object],
        /,
    ) -> Mapping[str, object] | None:
        """Return one detached exact document by immutable identity."""
        await asyncio.sleep(0)
        self.read_calls += 1
        if self.document is None or self.document.get("_id") != filter_document.get("_id"):
            return None
        return dict(self.document)


def _evidence_repository(
    collection: _FakeEvidenceCollection,
) -> CustomerGraphEvidenceRepository:
    """Build one repository with a deterministic bounded timeout."""
    return CustomerGraphEvidenceRepository(
        collection,
        operation_timeout_seconds=1.0,
    )


@pytest.mark.asyncio
async def test_builds_digest_bound_platform_graph_evidence_document() -> None:
    """Bind all sync, write, read-back, and idempotency digests to one record."""
    report = await _validate(_FakeExecutionPort())

    document = CustomerGraphEvidenceDocument.create(report)

    assert document.sync_run_id == report.sync_run_id
    assert document.report_digest == report.report_digest
    assert document.schema_evidence_digest == report.execution.schema_evidence_digest
    assert document.first_write_evidence_digest == report.execution.first_write_evidence_digest
    assert document.second_write_evidence_digest == report.execution.second_write_evidence_digest
    assert (
        document.first_readback_evidence_digest == report.execution.first_readback.evidence_digest
    )
    assert (
        document.second_readback_evidence_digest == report.execution.second_readback.evidence_digest
    )
    assert document.idempotency_evidence_digest == report.execution.idempotency.evidence_digest
    mongo_document = document.to_mongo_document()
    assert mongo_document["_id"] == document.document_id
    assert mongo_document["executed_at_epoch_microseconds"] == (
        document.executed_at_epoch_microseconds
    )


@pytest.mark.asyncio
async def test_prepares_fixed_indexes_and_persists_exactly_once() -> None:
    """Create the immutable aggregate once and accept an exact replay."""
    report = await _validate(_FakeExecutionPort())
    document = CustomerGraphEvidenceDocument.create(report)
    collection = _FakeEvidenceCollection()
    repository = _evidence_repository(collection)

    index_names = await repository.prepare_indexes()
    first = await repository.persist(document)
    second = await repository.persist(document)
    stored = await repository.get_by_sync_run_id(document.sync_run_id)

    assert index_names == (
        "ux_graph_evidence_report_digest",
        "ux_graph_evidence_sync_run_id",
        "ix_graph_evidence_executed_at_epoch_us",
        "ix_graph_evidence_executed_at_epoch_us_document_id",
        "ix_graph_evidence_source_executed_epoch_us",
    )
    assert first.status is CustomerGraphEvidencePersistenceStatus.CREATED
    assert second.status is (CustomerGraphEvidencePersistenceStatus.ALREADY_PRESENT)
    assert first.document_digest == document.document_digest
    assert stored == document
    assert collection.update_calls == 2
    assert collection.read_calls == 3


@pytest.mark.asyncio
async def test_rejects_different_evidence_for_existing_sync_run() -> None:
    """Fail closed instead of replacing an immutable run aggregate."""
    report = await _validate(_FakeExecutionPort())
    document = CustomerGraphEvidenceDocument.create(report)
    collection = _FakeEvidenceCollection()
    repository = _evidence_repository(collection)
    await repository.persist(document)
    assert collection.document is not None
    collection.document["document_digest"] = "f" * 64

    with pytest.raises(CustomerGraphEvidencePersistenceError) as exc_info:
        await repository.persist(document)

    assert exc_info.value.code is (CustomerGraphEvidencePersistenceErrorCode.IMMUTABLE_CONFLICT)


@pytest.mark.asyncio
async def test_persistence_preserves_caller_cancellation() -> None:
    """Do not convert caller cancellation into a persistence error."""
    report = await _validate(_FakeExecutionPort())
    document = CustomerGraphEvidenceDocument.create(report)
    collection = _FakeEvidenceCollection()
    collection.block_update = asyncio.Event()
    repository = _evidence_repository(collection)
    task = asyncio.create_task(repository.persist(document))
    await asyncio.sleep(0)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_document_rejects_digest_tampering() -> None:
    """Reject replacement of any immutable aggregate digest."""
    report = await _validate(_FakeExecutionPort())
    document = CustomerGraphEvidenceDocument.create(report)

    with pytest.raises(ValidationError):
        CustomerGraphEvidenceDocument.model_validate(
            {
                **document.model_dump(mode="python"),
                "document_digest": "f" * 64,
            }
        )


def test_runner_disables_retryable_writes_and_persists_before_success() -> None:
    """Enforce explicit retry ownership and persisted-success output."""
    source_path = (
        Path(__file__).parents[1] / "src/return_platform/data_platform/graph/sandbox_runner.py"
    )
    source = source_path.read_text(encoding="utf-8")

    assert "retryReads=False" in source
    assert "retryWrites=False" in source
    assert "CustomerGraphEvidenceRepository" in source
    assert "platform_evidence_document_digest" in source
    assert "with_transaction" not in source

    repository_path = (
        Path(__file__).parents[1] / "src/return_platform/data_platform/graph/evidence_repository.py"
    )
    repository_source = repository_path.read_text(encoding="utf-8")
    assert 'WriteConcern(w="majority", j=True)' in repository_source
    assert 'ReadConcern("majority")' in repository_source
    assert "ReadPreference.PRIMARY" in repository_source
    assert '"$setOnInsert"' in repository_source
    assert "with_transaction" not in repository_source


def test_runner_default_dotenv_path_is_repository_root() -> None:
    """Resolve the canonical dotenv file above backend, not inside it."""
    expected = Path(__file__).resolve().parents[2] / ".env"

    assert sandbox_runner.repository_dotenv_file() == expected


def test_runner_reports_missing_mongo_dsn_as_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Expose only the missing setting name and never configuration values."""
    missing_dotenv = tmp_path / "missing.env"
    monkeypatch.setattr(
        sandbox_runner,
        "repository_dotenv_file",
        lambda: missing_dotenv,
    )
    monkeypatch.setenv("PLATFORM_NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("PLATFORM_NEO4J_USER", "neo4j")
    monkeypatch.setenv(
        "PLATFORM_NEO4J_PASSWORD",
        "do-not-emit-this-secret",
    )
    monkeypatch.delenv("PLATFORM_MONGO_DSN", raising=False)

    exit_code = sandbox_runner.main(
        [
            "--source-file",
            str(Path(__file__).parent / "fixtures/customer_graph_sandbox/customer_p100.json"),
            "--source-document-id",
            "P100",
            "--source-updated-at",
            "2026-07-22T04:00:00Z",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err == (
        '{"error_code":"CONFIGURATION_INVALID",'
        '"invalid_fields":["mongo_dsn"],'
        '"process_exit_code":2,"status":"FAILED"}\n'
    )
    assert "do-not-emit-this-secret" not in captured.err


def test_runner_settings_load_values_from_explicit_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Load required settings from an isolated repository-style dotenv."""
    variable_names = (
        "PLATFORM_NEO4J_URI",
        "PLATFORM_NEO4J_USER",
        "PLATFORM_NEO4J_PASSWORD",
        "PLATFORM_MONGO_DSN",
    )
    for variable_name in variable_names:
        monkeypatch.delenv(variable_name, raising=False)

    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "\n".join(
            (
                "PLATFORM_NEO4J_URI=bolt://localhost:7687",
                "PLATFORM_NEO4J_USER=neo4j",
                "PLATFORM_NEO4J_PASSWORD=dotenv-neo4j-secret",
                ("PLATFORM_MONGO_DSN=mongodb://localhost:27017/?replicaSet=rs0"),
                "PLATFORM_MONGO_DATABASE=return_platform",
                ("PLATFORM_GRAPH_EVIDENCE_COLLECTION=graph_evidence_runs"),
                "",
            )
        ),
        encoding="utf-8",
    )

    settings = sandbox_runner.load_customer_graph_sandbox_settings(dotenv_file=dotenv_path)

    assert settings.neo4j_uri == "bolt://localhost:7687"
    assert settings.neo4j_user == "neo4j"
    assert settings.neo4j_password.get_secret_value() == "dotenv-neo4j-secret"
    assert settings.mongo_dsn.get_secret_value() == ("mongodb://localhost:27017/?replicaSet=rs0")
    assert settings.mongo_database == "return_platform"
    assert settings.graph_evidence_collection == "graph_evidence_runs"


class _FakeGraphEvidenceQueryCollection:
    """Record fixed read-only query calls and return controlled documents."""

    def __init__(
        self,
        *,
        full_document: Mapping[str, object] | None = None,
        page_documents: tuple[Mapping[str, object], ...] = (),
    ) -> None:
        """Store controlled full and projected query results."""
        self.full_document = full_document
        self.page_documents = page_documents
        self.find_one_calls: list[tuple[dict[str, object], dict[str, int] | None]] = []
        self.find_many_calls: list[
            tuple[
                dict[str, object],
                dict[str, int],
                list[tuple[str, int]],
                int,
            ]
        ] = []
        self.block_reads: asyncio.Event | None = None

    async def find_one(
        self,
        filter_document: dict[str, object],
        /,
        *,
        projection: dict[str, int] | None,
    ) -> Mapping[str, object] | None:
        """Return one controlled full document for an exact filter."""
        self.find_one_calls.append((dict(filter_document), projection))
        if self.block_reads is not None:
            await self.block_reads.wait()
        if self.full_document is None:
            return None
        if "_id" in filter_document:
            if self.full_document.get("_id") != filter_document["_id"]:
                return None
        if "report_digest" in filter_document:
            if self.full_document.get("report_digest") != filter_document["report_digest"]:
                return None
        return dict(self.full_document)

    async def find_many(
        self,
        filter_document: dict[str, object],
        /,
        *,
        projection: dict[str, int],
        sort: list[tuple[str, int]],
        limit: int,
    ) -> tuple[Mapping[str, object], ...]:
        """Return one controlled bounded page and record the fixed query."""
        self.find_many_calls.append(
            (
                dict(filter_document),
                dict(projection),
                list(sort),
                limit,
            )
        )
        if self.block_reads is not None:
            await self.block_reads.wait()
        return self.page_documents[:limit]


def _query_repository(
    collection: _FakeGraphEvidenceQueryCollection,
) -> CustomerGraphEvidenceQueryRepository:
    """Build one bounded read-only graph-evidence query repository."""
    return CustomerGraphEvidenceQueryRepository(
        collection,
        operation_timeout_seconds=1.0,
    )


def _summary_projection(
    *,
    document_id: str,
    sync_run_id: UUID,
    executed_at: datetime,
) -> dict[str, object]:
    """Build one exact fixed-query summary projection."""
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = executed_at - epoch
    executed_at_epoch_microseconds = (
        delta.days * 86_400 * 1_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    )
    return {
        "_id": document_id,
        "schema_version": "1.0",
        "evidence_type": "CUSTOMER_GRAPH_SANDBOX_RUN",
        "report_digest": "1" * 64,
        "document_digest": "2" * 64,
        "sync_run_id": str(sync_run_id),
        "executed_at": (
            executed_at.isoformat(timespec="microseconds").replace(
                "+00:00",
                "Z",
            )
        ),
        "executed_at_epoch_microseconds": executed_at_epoch_microseconds,
        "source_document_id": "P100",
        "source_hash": "3" * 64,
        "configuration_digest": "4" * 64,
        "execution_plan_digest": "5" * 64,
        "command_batch_digest": "6" * 64,
        "report_payload": {
            "evidence_classification": "SANDBOX_VALIDATED",
            "expected_customer_count": 1,
            "expected_customer_account_count": 2,
            "expected_relationship_count": 2,
            "execution": {"idempotency": {"idempotent": True}},
        },
    }


@pytest.mark.asyncio
async def test_query_repository_supports_only_exact_full_lookups() -> None:
    """Validate full documents for document, run, and report identities."""
    report = await _validate(_FakeExecutionPort())
    document = CustomerGraphEvidenceDocument.create(report)
    collection = _FakeGraphEvidenceQueryCollection(full_document=document.to_mongo_document())
    repository = _query_repository(collection)

    by_document = await repository.get_by_document_id(document.document_id)
    by_run = await repository.get_by_sync_run_id(document.sync_run_id)
    by_report = await repository.get_by_report_digest(document.report_digest)

    assert by_document == document
    assert by_run == document
    assert by_report == document
    assert collection.find_one_calls == [
        ({"_id": document.document_id}, None),
        ({"_id": document.document_id}, None),
        ({"report_digest": document.report_digest}, None),
    ]


@pytest.mark.asyncio
async def test_query_repository_uses_bounded_seek_pagination() -> None:
    """Use the compound ordering key and never offset-based pagination."""
    first_run = UUID("10000000-0000-4000-8000-000000000001")
    second_run = UUID("10000000-0000-4000-8000-000000000002")
    first_document_id = f"CUSTOMER_GRAPH_SANDBOX:{first_run}"
    second_document_id = f"CUSTOMER_GRAPH_SANDBOX:{second_run}"
    first_projection = _summary_projection(
        document_id=first_document_id,
        sync_run_id=first_run,
        executed_at=datetime(2026, 7, 22, 10, 0, tzinfo=UTC),
    )
    second_projection = _summary_projection(
        document_id=second_document_id,
        sync_run_id=second_run,
        executed_at=datetime(2026, 7, 22, 9, 0, tzinfo=UTC),
    )
    collection = _FakeGraphEvidenceQueryCollection(
        page_documents=(first_projection, second_projection)
    )
    repository = _query_repository(collection)

    first_page = await repository.list_summaries(page_size=1, cursor=None)

    assert len(first_page.items) == 1
    assert first_page.has_more is True
    assert first_page.next_cursor is not None
    decoded = decode_customer_graph_evidence_cursor(first_page.next_cursor)
    assert decoded is not None
    assert decoded.document_id == first_document_id
    assert collection.find_many_calls[0][0] == {}
    assert collection.find_many_calls[0][2] == [
        ("executed_at_epoch_microseconds", -1),
        ("_id", -1),
    ]
    assert collection.find_many_calls[0][3] == 2

    collection.page_documents = (second_projection,)
    second_page = await repository.list_summaries(
        page_size=1,
        cursor=first_page.next_cursor,
    )

    assert second_page.items[0].document_id == second_document_id
    assert collection.find_many_calls[1][0] == {
        "$or": [
            {"executed_at_epoch_microseconds": {"$lt": decoded.executed_at_epoch_microseconds}},
            {
                "executed_at_epoch_microseconds": (decoded.executed_at_epoch_microseconds),
                "_id": {"$lt": decoded.document_id},
            },
        ]
    }


@pytest.mark.asyncio
async def test_query_repository_rejects_projection_identity_mismatch() -> None:
    """Reject a plausible projection whose document and run IDs diverge."""
    sync_run_id = UUID("10000000-0000-4000-8000-000000000001")
    projection = _summary_projection(
        document_id=("CUSTOMER_GRAPH_SANDBOX:10000000-0000-4000-8000-000000000002"),
        sync_run_id=sync_run_id,
        executed_at=datetime(2026, 7, 22, 10, 0, tzinfo=UTC),
    )
    repository = _query_repository(_FakeGraphEvidenceQueryCollection(page_documents=(projection,)))

    with pytest.raises(CustomerGraphEvidenceQueryError) as exc_info:
        await repository.list_summaries(page_size=25, cursor=None)

    assert exc_info.value.code is (CustomerGraphEvidenceQueryErrorCode.EVIDENCE_INVALID)


@pytest.mark.asyncio
async def test_query_repository_rejects_projection_timestamp_mismatch() -> None:
    """Reject a plausible projection with a detached ordering timestamp."""
    sync_run_id = UUID("10000000-0000-4000-8000-000000000001")
    projection = _summary_projection(
        document_id=f"CUSTOMER_GRAPH_SANDBOX:{sync_run_id}",
        sync_run_id=sync_run_id,
        executed_at=datetime(2026, 7, 22, 10, 0, tzinfo=UTC),
    )
    projection["executed_at_epoch_microseconds"] = 1
    repository = _query_repository(_FakeGraphEvidenceQueryCollection(page_documents=(projection,)))

    with pytest.raises(CustomerGraphEvidenceQueryError) as exc_info:
        await repository.list_summaries(page_size=25, cursor=None)

    assert exc_info.value.code is (CustomerGraphEvidenceQueryErrorCode.EVIDENCE_INVALID)


@pytest.mark.asyncio
async def test_query_repository_rejects_noncanonical_cursor() -> None:
    """Reject malformed, noncanonical, and tampered pagination cursors."""
    repository = _query_repository(_FakeGraphEvidenceQueryCollection())

    with pytest.raises(CustomerGraphEvidenceQueryError) as exc_info:
        await repository.list_summaries(
            page_size=25,
            cursor="not-a-canonical-cursor",
        )

    assert exc_info.value.code is (CustomerGraphEvidenceQueryErrorCode.CURSOR_INVALID)


@pytest.mark.asyncio
async def test_query_repository_preserves_caller_cancellation() -> None:
    """Do not convert caller cancellation into a query failure."""
    collection = _FakeGraphEvidenceQueryCollection()
    collection.block_reads = asyncio.Event()
    repository = _query_repository(collection)
    task = asyncio.create_task(repository.list_summaries(page_size=25, cursor=None))
    await asyncio.sleep(0)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


def test_query_source_has_no_arbitrary_filter_or_offset_entry_point() -> None:
    """Keep all read filters, projections, and ordering code-owned."""
    source_path = (
        Path(__file__).parents[1] / "src/return_platform/data_platform/graph/evidence_query.py"
    )
    source = source_path.read_text(encoding="utf-8")

    assert '"$where"' not in source
    assert ".aggregate(" not in source
    assert ".skip(" not in source
    assert "caller_filter" not in source
    assert "retryReads" not in source
    assert "retryWrites" not in source

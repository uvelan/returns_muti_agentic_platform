"""Adversarial tests for sandbox-only Customer graph validation orchestration."""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from return_platform.data_platform.graph import sandbox_runner
from return_platform.data_platform.graph.commands import (
    CustomerNeo4jCommandBatch,
    Neo4jNodeUpsertCommand,
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
    link = tmp_path / "link.json"
    link.symlink_to(target)
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{" + b" " * 1_048_576 + b"}")

    for path in (link, oversized):
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

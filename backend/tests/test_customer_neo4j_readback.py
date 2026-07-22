"""Adversarial tests for fixed Customer graph read-back validation."""

from __future__ import annotations

import ast
import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, TypedDict, Unpack, cast
from uuid import UUID

import pytest
from neo4j import READ_ACCESS, AsyncDriver, Bookmarks
from pydantic import ValidationError

from return_platform.canonical import (
    GraphProjectionEvidence,
    GraphProjectionStatus,
)
from return_platform.data_platform.graph.commands import (
    CustomerNeo4jCommandBatch,
    build_customer_neo4j_commands,
)
from return_platform.data_platform.graph.readback import (
    CUSTOMER_ACCOUNT_READBACK_CYPHER,
    CUSTOMER_READBACK_CYPHER,
    HAS_ACCOUNT_READBACK_CYPHER,
    CustomerGraphReadbackError,
    CustomerGraphReadbackErrorCode,
    CustomerGraphReadbackEvidence,
    CustomerGraphReadbackValidator,
    CustomerNodeReadback,
    CustomerRelationshipReadback,
    assert_customer_graph_idempotency,
    validate_customer_graph_snapshot_records,
)
from return_platform.data_platform.graph.writer import (
    CustomerNeo4jDataWriteEvidence,
    CustomerNeo4jWriter,
)
from return_platform.data_platform.mapping.projection import (
    CustomerGraphProjectionMaterialization,
    GraphNodeUpsertParameters,
    GraphParameterMap,
    GraphRelationshipUpsertParameters,
)

_SYNC_RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
_GRAPH_SYNCED_AT = datetime(2026, 7, 22, 5, 30, tzinfo=UTC)
_SOURCE_UPDATED_AT = datetime(2026, 7, 20, 1, 2, tzinfo=UTC)
_DIGEST = "a" * 64
_WRITE_DIGEST_1 = "b" * 64
_WRITE_DIGEST_2 = "c" * 64
_CUSTOMER_KEY = "CUSTOMER_CDM:P100"
_ACCOUNT_KEYS = ("CUSTOMER_CDM:202*C001", "CUSTOMER_CDM:203*C002")
_DATABASE = "neo4j"
_EXPECTED_NODE_COUNT = 3
_EXPECTED_RELATIONSHIP_COUNT = 2


class _MutableReadbackEvidence(Protocol):
    evidence_digest: str


class _BeginTransactionOptions(TypedDict, total=False):
    timeout: float | None


class _FakeResult:
    """Result supporting the exact writer and read-back methods under test."""

    def __init__(
        self,
        *,
        record: Mapping[str, object] | None = None,
        records: tuple[Mapping[str, object], ...] = (),
        wait_event: asyncio.Event | None = None,
    ) -> None:
        self.record = record
        self.records = records
        self.wait_event = wait_event
        self.consume_calls = 0
        self.single_calls = 0
        self.fetch_calls: list[int] = []

    async def consume(self) -> None:
        """Record schema-result consumption."""
        self.consume_calls += 1

    async def single(
        self,
        *,
        strict: bool = False,
    ) -> Mapping[str, object] | None:
        """Return one deterministic write identity record."""
        del strict
        self.single_calls += 1
        return self.record

    async def fetch(self, count: int) -> list[Mapping[str, object]]:
        """Return bounded records or wait until cancelled for timeout tests."""
        self.fetch_calls.append(count)
        if self.wait_event is not None:
            await self.wait_event.wait()
        return list(self.records[:count])


class _FakeTransaction:
    """Ordered explicit transaction with no hidden retries."""

    def __init__(self, results: tuple[_FakeResult, ...]) -> None:
        self.results = list(results)
        self.run_calls: list[tuple[str, dict[str, object]]] = []
        self.commit_calls = 0
        self.rollback_calls = 0
        self.cancel_calls = 0
        self._closed = False

    async def run(
        self,
        query: str,
        parameters: Mapping[str, object] | None = None,
    ) -> _FakeResult:
        """Return the next configured result and capture exact query inputs."""
        self.run_calls.append((query, dict(parameters or {})))
        if not self.results:
            raise AssertionError("unexpected transaction query")
        return self.results.pop(0)

    async def commit(self) -> None:
        """Commit one explicit transaction."""
        self.commit_calls += 1
        self._closed = True

    async def rollback(self) -> None:
        """Rollback one explicit transaction."""
        self.rollback_calls += 1
        self._closed = True

    def cancel(self) -> None:
        """Cancel one explicit transaction."""
        self.cancel_calls += 1
        self._closed = True

    def closed(self) -> bool:
        """Return whether the transaction is closed."""
        return self._closed


class _FakeSession:
    """One short-lived session returning one explicit transaction."""

    def __init__(
        self,
        transaction: _FakeTransaction,
        *,
        bookmark_values: tuple[str, ...],
    ) -> None:
        self.transaction = transaction
        self.bookmark_values = bookmark_values
        self.begin_calls: list[tuple[dict[str, object] | None, float | None]] = []
        self.close_calls = 0
        self.cancel_calls = 0

    async def begin_transaction(
        self,
        metadata: dict[str, object] | None = None,
        **options: Unpack[_BeginTransactionOptions],
    ) -> _FakeTransaction:
        """Start exactly one unmanaged transaction."""
        self.begin_calls.append((metadata, options.get("timeout")))
        return self.transaction

    async def last_bookmarks(self) -> Bookmarks:
        """Return deterministic causal bookmarks."""
        return Bookmarks.from_raw_values(self.bookmark_values)

    async def close(self) -> None:
        """Close the session."""
        self.close_calls += 1

    def cancel(self) -> None:
        """Cancel the session."""
        self.cancel_calls += 1

    def closed(self) -> bool:
        """Return whether the session is already closed or cancelled."""
        return self.close_calls > 0 or self.cancel_calls > 0


class _FakeDriver:
    """Injected driver returning sessions in deterministic order."""

    def __init__(self, *sessions: _FakeSession) -> None:
        self.sessions = list(sessions)
        self.session_calls: list[dict[str, object]] = []
        self.close_calls = 0

    def session(self, **kwargs: object) -> _FakeSession:
        """Return the next configured session."""
        self.session_calls.append(dict(kwargs))
        if not self.sessions:
            raise AssertionError("unexpected session request")
        return self.sessions.pop(0)

    async def close(self) -> None:
        """Record prohibited component-level driver ownership."""
        self.close_calls += 1


def _customer_properties() -> GraphParameterMap:
    """Build exact Customer graph properties."""
    return GraphParameterMap.from_mapping(
        {
            "canonical_key": _CUSTOMER_KEY,
            "configuration_digest": _DIGEST,
            "customer_key": _CUSTOMER_KEY,
            "graph_synced_at": _GRAPH_SYNCED_AT,
            "identity_quality": "VERIFIED",
            "mapping_version": "1.0",
            "party_id": "P100",
            "source_asset": "customerOutboundCDM",
            "source_database": "eventMessages",
            "source_record_id": "P100",
            "source_system": "CUSTOMER_CDM",
            "source_updated_at": _SOURCE_UPDATED_AT,
            "sync_run_id": str(_SYNC_RUN_ID),
        }
    )


def _account_properties(account_key: str) -> GraphParameterMap:
    """Build exact CustomerAccount graph properties."""
    account_number = account_key.removeprefix("CUSTOMER_CDM:")
    return GraphParameterMap.from_mapping(
        {
            "account_key": account_key,
            "account_number": account_number,
            "canonical_key": account_key,
            "configuration_digest": _DIGEST,
            "customer_key": _CUSTOMER_KEY,
            "graph_synced_at": _GRAPH_SYNCED_AT,
            "identity_quality": "VERIFIED",
            "mapping_version": "1.0",
            "source_asset": "customerOutboundCDM",
            "source_database": "eventMessages",
            "source_record_id": account_number,
            "source_system": "CUSTOMER_CDM",
            "source_updated_at": _SOURCE_UPDATED_AT,
            "sync_run_id": str(_SYNC_RUN_ID),
        }
    )


def _materialization() -> CustomerGraphProjectionMaterialization:
    """Build one valid Customer graph materialization with two accounts."""
    customer_node = GraphNodeUpsertParameters(
        node_mapping_id="graph.customer.v1",
        label="Customer",
        key_property="customer_key",
        key_value=_CUSTOMER_KEY,
        properties=_customer_properties(),
    )
    account_nodes = tuple(
        GraphNodeUpsertParameters(
            node_mapping_id="graph.customer_account.v1",
            label="CustomerAccount",
            key_property="account_key",
            key_value=account_key,
            properties=_account_properties(account_key),
        )
        for account_key in _ACCOUNT_KEYS
    )
    relationships = tuple(
        GraphRelationshipUpsertParameters(
            relationship_mapping_id="graph.customer.has_account.v1",
            relationship_type="HAS_ACCOUNT",
            source_node_mapping_id="graph.customer.v1",
            source_label="Customer",
            source_key_property="customer_key",
            source_key_value=_CUSTOMER_KEY,
            source_match=GraphParameterMap.from_mapping({"customer_key": _CUSTOMER_KEY}),
            target_node_mapping_id="graph.customer_account.v1",
            target_label="CustomerAccount",
            target_key_property="account_key",
            target_key_value=account_key,
            target_match=GraphParameterMap.from_mapping({"account_key": account_key}),
        )
        for account_key in _ACCOUNT_KEYS
    )
    evidence = GraphProjectionEvidence(
        evidence_id=UUID("22222222-2222-4222-8222-222222222222"),
        sync_run_id=_SYNC_RUN_ID,
        source_asset="customerOutboundCDM",
        source_record_id="P100",
        canonical_entity_type="Customer",
        canonical_entity_key=_CUSTOMER_KEY,
        graph_label="Customer",
        graph_key=_CUSTOMER_KEY,
        mapping_version="1.0",
        projection_status=GraphProjectionStatus.PROJECTED,
        projected_at=_GRAPH_SYNCED_AT,
    )
    return CustomerGraphProjectionMaterialization(
        materializer_version="1.0",
        execution_plan_digest=_DIGEST,
        sync_run_id=_SYNC_RUN_ID,
        graph_synced_at=_GRAPH_SYNCED_AT,
        customer_node=customer_node,
        customer_account_nodes=account_nodes,
        has_account_relationships=relationships,
        projection_evidence=(evidence,),
    )


def _batch() -> CustomerNeo4jCommandBatch:
    """Build the fixed deterministic command batch."""
    return build_customer_neo4j_commands(_materialization())


def _utc_text(value: datetime) -> str:
    """Return a Neo4j-compatible ISO UTC string."""
    return value.isoformat().replace("+00:00", "Z")


def _node_record(
    *,
    label: str,
    key: str,
    match_count: int = 1,
    configuration_digest: str = _DIGEST,
    graph_synced_at: datetime = _GRAPH_SYNCED_AT,
) -> Mapping[str, object]:
    """Build one fixed-query node result row."""
    key_name = "customer_key" if label == "Customer" else "account_key"
    source_record_id = "P100" if label == "Customer" else key.removeprefix("CUSTOMER_CDM:")
    return {
        key_name: key,
        "match_count": match_count,
        "canonical_key": key,
        "configuration_digest": configuration_digest,
        "graph_synced_at": _utc_text(graph_synced_at),
        "identity_quality": "VERIFIED",
        "mapping_version": "1.0",
        "source_asset": "customerOutboundCDM",
        "source_database": "eventMessages",
        "source_record_id": source_record_id,
        "source_system": "CUSTOMER_CDM",
        "source_updated_at": _utc_text(_SOURCE_UPDATED_AT),
        "sync_run_id": str(_SYNC_RUN_ID),
    }


def _relationship_record(
    account_key: str,
    *,
    match_count: int = 1,
) -> Mapping[str, object]:
    """Build one fixed-query HAS_ACCOUNT result row."""
    return {
        "account_key": account_key,
        "match_count": match_count,
        "source_key": _CUSTOMER_KEY,
        "target_key": account_key,
    }


def _validated_snapshot() -> tuple[
    CustomerNodeReadback,
    tuple[CustomerNodeReadback, ...],
    tuple[CustomerRelationshipReadback, ...],
]:
    """Return one valid snapshot through the public deterministic validator."""
    customer, accounts, relationships = validate_customer_graph_snapshot_records(
        batch=_batch(),
        customer_record=_node_record(label="Customer", key=_CUSTOMER_KEY),
        account_records=tuple(
            _node_record(label="CustomerAccount", key=key) for key in _ACCOUNT_KEYS
        ),
        relationship_records=tuple(_relationship_record(key) for key in _ACCOUNT_KEYS),
    )
    return customer, accounts, relationships


def test_fixed_queries_are_parameterized_and_code_owned() -> None:
    """Prevent arbitrary labels, keys, or Cypher fragments from entering reads."""
    assert "$customer_key" in CUSTOMER_READBACK_CYPHER
    assert "$account_keys" in CUSTOMER_ACCOUNT_READBACK_CYPHER
    assert "$customer_key" in HAS_ACCOUNT_READBACK_CYPHER
    assert "$account_keys" in HAS_ACCOUNT_READBACK_CYPHER
    assert "Customer" in CUSTOMER_READBACK_CYPHER
    assert "CustomerAccount" in CUSTOMER_ACCOUNT_READBACK_CYPHER
    assert "HAS_ACCOUNT" in HAS_ACCOUNT_READBACK_CYPHER


def test_validates_exact_nodes_relationships_and_provenance() -> None:
    """Accept only the exact expected canonical keys and provenance."""
    customer, accounts, relationships = _validated_snapshot()

    assert customer.key == _CUSTOMER_KEY
    assert len(accounts) == len(_ACCOUNT_KEYS)
    assert len(relationships) == len(_ACCOUNT_KEYS)
    assert tuple(item.key for item in accounts) == _ACCOUNT_KEYS
    assert tuple(item.target_key for item in relationships) == _ACCOUNT_KEYS


@pytest.mark.parametrize(
    ("customer_count", "account_count", "relationship_count"),
    [(0, 1, 1), (2, 1, 1), (1, 0, 1), (1, 2, 1), (1, 1, 0), (1, 1, 2)],
)
def test_rejects_missing_or_duplicate_graph_entities(
    customer_count: int,
    account_count: int,
    relationship_count: int,
) -> None:
    """Reject both missing and duplicate nodes or relationships."""
    with pytest.raises(CustomerGraphReadbackError) as exc_info:
        validate_customer_graph_snapshot_records(
            batch=_batch(),
            customer_record=_node_record(
                label="Customer",
                key=_CUSTOMER_KEY,
                match_count=customer_count,
            ),
            account_records=(
                _node_record(
                    label="CustomerAccount",
                    key=_ACCOUNT_KEYS[0],
                    match_count=account_count,
                ),
                _node_record(label="CustomerAccount", key=_ACCOUNT_KEYS[1]),
            ),
            relationship_records=(
                _relationship_record(
                    _ACCOUNT_KEYS[0],
                    match_count=relationship_count,
                ),
                _relationship_record(_ACCOUNT_KEYS[1]),
            ),
        )

    assert exc_info.value.code is (CustomerGraphReadbackErrorCode.RESULT_CARDINALITY_INVALID)


def test_rejects_swapped_account_rows_even_when_values_are_plausible() -> None:
    """Reject a nondeterministic result order instead of comparing as a set."""
    with pytest.raises(CustomerGraphReadbackError) as exc_info:
        validate_customer_graph_snapshot_records(
            batch=_batch(),
            customer_record=_node_record(label="Customer", key=_CUSTOMER_KEY),
            account_records=tuple(
                _node_record(label="CustomerAccount", key=key) for key in reversed(_ACCOUNT_KEYS)
            ),
            relationship_records=tuple(_relationship_record(key) for key in _ACCOUNT_KEYS),
        )

    assert exc_info.value.code is CustomerGraphReadbackErrorCode.RESULT_VALUE_INVALID


def test_rejects_stale_or_foreign_mandatory_provenance() -> None:
    """Reject a plausible canonical key backed by stale or foreign provenance."""
    with pytest.raises(CustomerGraphReadbackError) as stale_error:
        validate_customer_graph_snapshot_records(
            batch=_batch(),
            customer_record=_node_record(
                label="Customer",
                key=_CUSTOMER_KEY,
                graph_synced_at=_GRAPH_SYNCED_AT - timedelta(seconds=1),
            ),
            account_records=tuple(
                _node_record(label="CustomerAccount", key=key) for key in _ACCOUNT_KEYS
            ),
            relationship_records=tuple(_relationship_record(key) for key in _ACCOUNT_KEYS),
        )
    with pytest.raises(CustomerGraphReadbackError) as foreign_error:
        validate_customer_graph_snapshot_records(
            batch=_batch(),
            customer_record=_node_record(label="Customer", key=_CUSTOMER_KEY),
            account_records=(
                _node_record(
                    label="CustomerAccount",
                    key=_ACCOUNT_KEYS[0],
                    configuration_digest="f" * 64,
                ),
                _node_record(label="CustomerAccount", key=_ACCOUNT_KEYS[1]),
            ),
            relationship_records=tuple(_relationship_record(key) for key in _ACCOUNT_KEYS),
        )

    assert stale_error.value.code is (CustomerGraphReadbackErrorCode.PROVENANCE_MISMATCH)
    assert foreign_error.value.code is (CustomerGraphReadbackErrorCode.PROVENANCE_MISMATCH)


def test_readback_evidence_is_digest_bound_and_idempotency_is_exact() -> None:
    """Bind the graph snapshot and prove an unchanged second execution."""
    customer, accounts, relationships = validate_customer_graph_snapshot_records(
        batch=_batch(),
        customer_record=_node_record(label="Customer", key=_CUSTOMER_KEY),
        account_records=tuple(
            _node_record(label="CustomerAccount", key=key) for key in _ACCOUNT_KEYS
        ),
        relationship_records=tuple(_relationship_record(key) for key in _ACCOUNT_KEYS),
    )
    started_at = datetime(2026, 7, 22, 6, 0, tzinfo=UTC)
    first = CustomerGraphReadbackEvidence.create(
        command_batch_digest=_batch().command_batch_digest,
        write_evidence_digest=_WRITE_DIGEST_1,
        execution_plan_digest=_DIGEST,
        sync_run_id=_SYNC_RUN_ID,
        database=_DATABASE,
        transaction_timeout_seconds=2.0,
        operation_timeout_seconds=3.0,
        started_at=started_at,
        completed_at=started_at + timedelta(milliseconds=1),
        customer=customer,
        customer_accounts=accounts,
        has_account_relationships=relationships,
    )
    second = CustomerGraphReadbackEvidence.create(
        command_batch_digest=_batch().command_batch_digest,
        write_evidence_digest=_WRITE_DIGEST_2,
        execution_plan_digest=_DIGEST,
        sync_run_id=_SYNC_RUN_ID,
        database=_DATABASE,
        transaction_timeout_seconds=2.0,
        operation_timeout_seconds=3.0,
        started_at=started_at + timedelta(seconds=1),
        completed_at=started_at + timedelta(seconds=1, milliseconds=1),
        customer=customer,
        customer_accounts=accounts,
        has_account_relationships=relationships,
    )

    evidence = assert_customer_graph_idempotency(
        first,
        second,
        first_write_evidence_digest=_WRITE_DIGEST_1,
        second_write_evidence_digest=_WRITE_DIGEST_2,
    )

    assert evidence.idempotent is True
    assert evidence.first_customer_count == 1
    assert evidence.second_customer_account_count == len(_ACCOUNT_KEYS)
    assert evidence.second_relationship_count == len(_ACCOUNT_KEYS)


def test_idempotency_rejects_same_counts_with_changed_provenance() -> None:
    """Reject equal counts when the second graph snapshot changed silently."""
    customer, accounts, relationships = validate_customer_graph_snapshot_records(
        batch=_batch(),
        customer_record=_node_record(label="Customer", key=_CUSTOMER_KEY),
        account_records=tuple(
            _node_record(label="CustomerAccount", key=key) for key in _ACCOUNT_KEYS
        ),
        relationship_records=tuple(_relationship_record(key) for key in _ACCOUNT_KEYS),
    )
    changed_customer = customer.model_copy(
        update={"graph_synced_at": _GRAPH_SYNCED_AT + timedelta(seconds=1)}
    )
    started_at = datetime(2026, 7, 22, 6, 0, tzinfo=UTC)
    first = CustomerGraphReadbackEvidence.create(
        command_batch_digest=_batch().command_batch_digest,
        write_evidence_digest=_WRITE_DIGEST_1,
        execution_plan_digest=_DIGEST,
        sync_run_id=_SYNC_RUN_ID,
        database=_DATABASE,
        transaction_timeout_seconds=2.0,
        operation_timeout_seconds=3.0,
        started_at=started_at,
        completed_at=started_at + timedelta(milliseconds=1),
        customer=customer,
        customer_accounts=accounts,
        has_account_relationships=relationships,
    )
    second = CustomerGraphReadbackEvidence.create(
        command_batch_digest=_batch().command_batch_digest,
        write_evidence_digest=_WRITE_DIGEST_2,
        execution_plan_digest=_DIGEST,
        sync_run_id=_SYNC_RUN_ID,
        database=_DATABASE,
        transaction_timeout_seconds=2.0,
        operation_timeout_seconds=3.0,
        started_at=started_at + timedelta(seconds=1),
        completed_at=started_at + timedelta(seconds=1, milliseconds=1),
        customer=changed_customer,
        customer_accounts=accounts,
        has_account_relationships=relationships,
    )

    with pytest.raises(CustomerGraphReadbackError) as exc_info:
        assert_customer_graph_idempotency(
            first,
            second,
            first_write_evidence_digest=_WRITE_DIGEST_1,
            second_write_evidence_digest=_WRITE_DIGEST_2,
        )

    assert exc_info.value.code is (CustomerGraphReadbackErrorCode.IDEMPOTENCY_MISMATCH)


def test_readback_evidence_rejects_digest_tampering() -> None:
    """Reject replacement of immutable read-back evidence fields or digest."""
    customer, accounts, relationships = validate_customer_graph_snapshot_records(
        batch=_batch(),
        customer_record=_node_record(label="Customer", key=_CUSTOMER_KEY),
        account_records=tuple(
            _node_record(label="CustomerAccount", key=key) for key in _ACCOUNT_KEYS
        ),
        relationship_records=tuple(_relationship_record(key) for key in _ACCOUNT_KEYS),
    )
    started_at = datetime(2026, 7, 22, 6, 0, tzinfo=UTC)
    evidence = CustomerGraphReadbackEvidence.create(
        command_batch_digest=_batch().command_batch_digest,
        write_evidence_digest=_WRITE_DIGEST_1,
        execution_plan_digest=_DIGEST,
        sync_run_id=_SYNC_RUN_ID,
        database=_DATABASE,
        transaction_timeout_seconds=2.0,
        operation_timeout_seconds=3.0,
        started_at=started_at,
        completed_at=started_at + timedelta(milliseconds=1),
        customer=customer,
        customer_accounts=accounts,
        has_account_relationships=relationships,
    )
    mutable = cast(_MutableReadbackEvidence, evidence)

    with pytest.raises(ValidationError):
        mutable.evidence_digest = "f" * 64
    with pytest.raises(ValidationError):
        CustomerGraphReadbackEvidence.model_validate(
            {**evidence.model_dump(mode="python"), "evidence_digest": "f" * 64}
        )


async def _writer_evidence_and_reader(
    *,
    delayed_read: bool = False,
) -> tuple[
    CustomerGraphReadbackValidator,
    CustomerNeo4jDataWriteEvidence,
    _FakeTransaction,
    _FakeSession,
    _FakeDriver,
]:
    """Produce public writer evidence and a configured read-back boundary."""
    batch = _batch()
    schema_transaction = _FakeTransaction((_FakeResult(), _FakeResult()))
    data_records = (
        {"key": _CUSTOMER_KEY},
        *({"key": key} for key in _ACCOUNT_KEYS),
        *({"source_key": _CUSTOMER_KEY, "target_key": key} for key in _ACCOUNT_KEYS),
    )
    data_transaction = _FakeTransaction(
        tuple(_FakeResult(record=record) for record in data_records)
    )
    wait_event = asyncio.Event() if delayed_read else None
    read_results = (
        _FakeResult(
            records=(_node_record(label="Customer", key=_CUSTOMER_KEY),),
            wait_event=wait_event,
        ),
        _FakeResult(
            records=tuple(_node_record(label="CustomerAccount", key=key) for key in _ACCOUNT_KEYS)
        ),
        _FakeResult(records=tuple(_relationship_record(key) for key in _ACCOUNT_KEYS)),
    )
    read_transaction = _FakeTransaction(read_results)
    read_session = _FakeSession(
        read_transaction,
        bookmark_values=("read-bookmark",),
    )
    driver = _FakeDriver(
        _FakeSession(schema_transaction, bookmark_values=("schema-bookmark",)),
        _FakeSession(data_transaction, bookmark_values=("data-bookmark",)),
        read_session,
    )
    writer = CustomerNeo4jWriter(cast(AsyncDriver, driver))
    schema_evidence = await writer.prepare_schema(
        batch=batch,
        database=_DATABASE,
        transaction_timeout_seconds=2.0,
        operation_timeout_seconds=3.0,
    )
    write_evidence = await writer.write_data(
        batch=batch,
        schema_evidence=schema_evidence,
        database=_DATABASE,
        transaction_timeout_seconds=2.0,
        operation_timeout_seconds=3.0,
    )
    return (
        CustomerGraphReadbackValidator(cast(AsyncDriver, driver)),
        write_evidence,
        read_transaction,
        read_session,
        driver,
    )


@pytest.mark.asyncio
async def test_live_boundary_uses_causal_bookmark_and_one_unmanaged_read() -> None:
    """Use committed write bookmarks and exactly three fixed read queries."""
    validator, write_evidence, transaction, _, driver = await _writer_evidence_and_reader()

    evidence = await validator.read_back(
        batch=_batch(),
        write_evidence=write_evidence,
        database=_DATABASE,
        transaction_timeout_seconds=2.0,
        operation_timeout_seconds=3.0,
    )

    assert evidence.customer_count == 1
    assert evidence.customer_account_count == len(_ACCOUNT_KEYS)
    assert evidence.relationship_count == len(_ACCOUNT_KEYS)
    assert transaction.commit_calls == 1
    assert transaction.rollback_calls == 0
    assert [query for query, _ in transaction.run_calls] == [
        CUSTOMER_READBACK_CYPHER,
        CUSTOMER_ACCOUNT_READBACK_CYPHER,
        HAS_ACCOUNT_READBACK_CYPHER,
    ]
    read_session_call = driver.session_calls[-1]
    assert read_session_call["default_access_mode"] == READ_ACCESS
    assert read_session_call["disable_auto_commit_retries"] is True
    assert driver.close_calls == 0


@pytest.mark.asyncio
async def test_outer_timeout_cancels_read_transaction_and_session() -> None:
    """Bound a hung read and preserve no-retry cancellation semantics."""
    validator, write_evidence, transaction, session, _ = await _writer_evidence_and_reader(
        delayed_read=True
    )

    with pytest.raises(CustomerGraphReadbackError) as exc_info:
        await validator.read_back(
            batch=_batch(),
            write_evidence=write_evidence,
            database=_DATABASE,
            transaction_timeout_seconds=0.05,
            operation_timeout_seconds=0.06,
        )

    assert exc_info.value.code is CustomerGraphReadbackErrorCode.TIMEOUT
    assert transaction.cancel_calls == 1
    assert session.cancel_calls == 1


def test_source_has_no_managed_retry_or_arbitrary_query_entry_point() -> None:
    """Reject managed retry APIs and caller-provided Cypher surfaces."""
    source_path = Path(__file__).parents[1] / "src/return_platform/data_platform/graph/readback.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

    assert "execute_read" not in attributes
    assert "execute_write" not in attributes
    assert "execute_query" not in attributes
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "close"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "_driver"
        for node in ast.walk(tree)
    )

"""Adversarial tests for the explicit no-retry Customer Neo4j writer."""

from __future__ import annotations

import ast
import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, TypedDict, Unpack, cast
from uuid import UUID

import pytest
from neo4j import WRITE_ACCESS, AsyncDriver, Bookmarks
from neo4j.exceptions import (
    AuthError,
    IncompleteCommit,
    Neo4jError,
    ResultNotSingleError,
    ServiceUnavailable,
)
from pydantic import ValidationError

from return_platform.canonical import (
    GraphProjectionEvidence,
    GraphProjectionStatus,
)
from return_platform.data_platform.graph.commands import (
    CUSTOMER_ACCOUNT_CONSTRAINT_CYPHER,
    CUSTOMER_ACCOUNT_NODE_UPSERT_CYPHER,
    CUSTOMER_CONSTRAINT_CYPHER,
    CUSTOMER_NODE_UPSERT_CYPHER,
    HAS_ACCOUNT_RELATIONSHIP_UPSERT_CYPHER,
    CustomerNeo4jCommandBatch,
    build_customer_neo4j_commands,
)
from return_platform.data_platform.graph.writer import (
    CustomerNeo4jWriter,
    Neo4jSchemaPreparationEvidence,
    Neo4jWriteError,
    Neo4jWriteErrorCode,
    Neo4jWritePhase,
)
from return_platform.data_platform.mapping.projection import (
    CustomerGraphProjectionMaterialization,
    GraphNodeUpsertParameters,
    GraphParameterMap,
    GraphRelationshipUpsertParameters,
)

_SYNC_RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
_GRAPH_SYNCED_AT = datetime(2026, 7, 22, 5, 30, tzinfo=UTC)
_DIGEST = "a" * 64
_CUSTOMER_KEY = "CUSTOMER_CDM:P100"
_ACCOUNT_KEY = "CUSTOMER_CDM:202*C001"
_DATABASE = "neo4j"


class _MutableSchemaEvidence(Protocol):
    """Writable view used only to verify frozen-model behavior."""

    database: str


class _BeginTransactionOptions(TypedDict, total=False):
    """Keyword options accepted by the fake transaction boundary."""

    timeout: float | None


class _ServerNeo4jError(Neo4jError):
    """Test-only server error carrying one explicit Neo4j status code."""

    def __init__(self, message: str, *, code: str) -> None:
        """Create one server-shaped Neo4j error without private driver APIs."""
        Exception.__init__(self, message)
        self._test_code = code

    @property
    def code(self) -> str:
        """Return the deterministic server error code."""
        return self._test_code


class _SequenceClock:
    """Deterministic clock returning predefined aware timestamps."""

    def __init__(self, *values: datetime) -> None:
        """Store deterministic clock values."""
        self._values = iter(values)

    def now(self) -> datetime:
        """Return the next configured UTC timestamp."""
        return next(self._values)


class _FakeResult:
    """Configurable async result."""

    def __init__(
        self,
        *,
        record: Mapping[str, object] | None = None,
        single_error: BaseException | None = None,
        consume_error: BaseException | None = None,
        wait_event: asyncio.Event | None = None,
    ) -> None:
        """Configure one fake result stream."""
        self.record = record
        self.single_error = single_error
        self.consume_error = consume_error
        self.wait_event = wait_event
        self.single_calls: list[bool] = []
        self.consume_calls = 0

    async def single(self, *, strict: bool = False) -> Mapping[str, object]:
        """Return one configured record or failure."""
        self.single_calls.append(strict)
        if self.wait_event is not None:
            await self.wait_event.wait()
        if self.single_error is not None:
            raise self.single_error
        if self.record is None:
            raise ResultNotSingleError("not one record")
        return self.record

    async def consume(self) -> object:
        """Consume the configured result."""
        self.consume_calls += 1
        if self.wait_event is not None:
            await self.wait_event.wait()
        if self.consume_error is not None:
            raise self.consume_error
        return object()


class _FakeTransaction:
    """One explicit transaction with ordered run outcomes."""

    def __init__(
        self,
        outcomes: list[_FakeResult | BaseException],
        *,
        commit_error: BaseException | None = None,
        rollback_error: BaseException | None = None,
    ) -> None:
        """Configure one fake explicit transaction."""
        self.outcomes = list(outcomes)
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.run_calls: list[tuple[str, dict[str, object]]] = []
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0
        self.cancel_calls = 0
        self._closed = False

    async def run(
        self,
        query: str,
        parameters: dict[str, object] | None = None,
    ) -> _FakeResult:
        """Record and execute one configured query outcome."""
        self.run_calls.append((query, {} if parameters is None else parameters))
        if not self.outcomes:
            raise AssertionError("unexpected query")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def commit(self) -> None:
        """Commit or raise the configured commit failure."""
        self.commit_calls += 1
        if self.commit_error is not None:
            if isinstance(self.commit_error, IncompleteCommit):
                self._closed = True
            raise self.commit_error
        self._closed = True

    async def rollback(self) -> None:
        """Rollback or raise the configured rollback failure."""
        self.rollback_calls += 1
        if self.rollback_error is not None:
            raise self.rollback_error
        self._closed = True

    async def close(self) -> None:
        """Close the fake transaction."""
        self.close_calls += 1
        self._closed = True

    def cancel(self) -> None:
        """Cancel the fake transaction."""
        self.cancel_calls += 1
        self._closed = True

    def closed(self) -> bool:
        """Return whether the fake transaction is closed."""
        return self._closed


class _FakeSession:
    """One short-lived async session."""

    def __init__(
        self,
        transaction: _FakeTransaction,
        *,
        bookmark_values: tuple[str, ...] = ("bookmark-1",),
    ) -> None:
        """Store one fake transaction and committed bookmarks."""
        self.transaction = transaction
        self.bookmark_values = bookmark_values
        self.begin_calls: list[tuple[dict[str, object] | None, float | None]] = []
        self.close_calls = 0
        self.cancel_calls = 0
        self.execute_write_calls = 0
        self.execute_query_calls = 0

    async def begin_transaction(
        self,
        metadata: dict[str, object] | None = None,
        **options: Unpack[_BeginTransactionOptions],
    ) -> _FakeTransaction:
        """Record one explicit transaction start."""
        self.begin_calls.append((metadata, options.get("timeout")))
        return self.transaction

    async def close(self) -> None:
        """Close the fake session."""
        self.close_calls += 1

    async def last_bookmarks(self) -> Bookmarks:
        """Return deterministic committed bookmarks."""
        return Bookmarks.from_raw_values(self.bookmark_values)

    def cancel(self) -> None:
        """Cancel the fake session."""
        self.cancel_calls += 1

    def closed(self) -> bool:
        """Return whether the fake session is closed."""
        return self.close_calls > 0 or self.cancel_calls > 0


class _FakeDriver:
    """Injected driver returning ordered sessions without owning close logic."""

    def __init__(
        self,
        *sessions: _FakeSession,
        session_error: BaseException | None = None,
    ) -> None:
        """Store ordered fake sessions or one creation failure."""
        self.sessions = list(sessions)
        self.session_error = session_error
        self.session_calls: list[dict[str, object]] = []
        self.close_calls = 0

    def session(self, **kwargs: object) -> _FakeSession:
        """Return the next configured fake session."""
        self.session_calls.append(dict(kwargs))
        if self.session_error is not None:
            raise self.session_error
        if not self.sessions:
            raise AssertionError("unexpected session")
        return self.sessions.pop(0)

    async def close(self) -> None:
        """Record prohibited driver ownership if called."""
        self.close_calls += 1


def _customer_properties() -> GraphParameterMap:
    """Build deterministic Customer graph properties."""
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
            "source_updated_at": datetime(2026, 7, 20, 1, 2, tzinfo=UTC),
            "sync_run_id": str(_SYNC_RUN_ID),
        }
    )


def _account_properties() -> GraphParameterMap:
    """Build deterministic CustomerAccount graph properties."""
    return GraphParameterMap.from_mapping(
        {
            "account_key": _ACCOUNT_KEY,
            "account_number": "202*C001",
            "canonical_key": _ACCOUNT_KEY,
            "configuration_digest": _DIGEST,
            "customer_key": _CUSTOMER_KEY,
            "graph_synced_at": _GRAPH_SYNCED_AT,
            "identity_quality": "VERIFIED",
            "mapping_version": "1.0",
            "source_asset": "customerOutboundCDM",
            "source_database": "eventMessages",
            "source_record_id": "202*C001",
            "source_system": "CUSTOMER_CDM",
            "source_updated_at": datetime(2026, 7, 20, 1, 2, tzinfo=UTC),
            "sync_run_id": str(_SYNC_RUN_ID),
        }
    )


def _materialization(
    *,
    include_data: bool = True,
) -> CustomerGraphProjectionMaterialization:
    """Build one valid Customer graph materialization fixture."""
    customer_node = GraphNodeUpsertParameters(
        node_mapping_id="graph.customer.v1",
        label="Customer",
        key_property="customer_key",
        key_value=_CUSTOMER_KEY,
        properties=_customer_properties(),
    )
    account_node = GraphNodeUpsertParameters(
        node_mapping_id="graph.customer_account.v1",
        label="CustomerAccount",
        key_property="account_key",
        key_value=_ACCOUNT_KEY,
        properties=_account_properties(),
    )
    relationship = GraphRelationshipUpsertParameters(
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
        target_key_value=_ACCOUNT_KEY,
        target_match=GraphParameterMap.from_mapping({"account_key": _ACCOUNT_KEY}),
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
        customer_node=customer_node if include_data else None,
        customer_account_nodes=(account_node,) if include_data else (),
        has_account_relationships=(relationship,) if include_data else (),
        projection_evidence=(evidence,),
    )


def _batch(*, include_data: bool = True) -> CustomerNeo4jCommandBatch:
    """Build one valid fixed Customer command batch."""
    return build_customer_neo4j_commands(_materialization(include_data=include_data))


def _clock() -> _SequenceClock:
    """Build a four-value deterministic execution clock."""
    start = datetime(2026, 7, 22, 6, 0, tzinfo=UTC)
    return _SequenceClock(
        start,
        start + timedelta(milliseconds=2),
        start + timedelta(milliseconds=3),
        start + timedelta(milliseconds=7),
    )


def _schema_transaction() -> _FakeTransaction:
    """Build one successful two-command schema transaction."""
    return _FakeTransaction([_FakeResult(), _FakeResult()])


def _data_transaction() -> _FakeTransaction:
    """Build one successful Customer data transaction."""
    return _FakeTransaction(
        [
            _FakeResult(record={"key": _CUSTOMER_KEY}),
            _FakeResult(record={"key": _ACCOUNT_KEY}),
            _FakeResult(
                record={
                    "source_key": _CUSTOMER_KEY,
                    "target_key": _ACCOUNT_KEY,
                }
            ),
        ]
    )


async def _prepare(
    *,
    batch: CustomerNeo4jCommandBatch | None = None,
    transaction: _FakeTransaction | None = None,
    clock: _SequenceClock | None = None,
) -> tuple[
    Neo4jSchemaPreparationEvidence,
    _FakeDriver,
    _FakeSession,
    _FakeTransaction,
    CustomerNeo4jWriter,
]:
    """Prepare schema and return all fake execution dependencies."""
    current_batch = _batch() if batch is None else batch
    current_transaction = _schema_transaction() if transaction is None else transaction
    session = _FakeSession(current_transaction)
    driver = _FakeDriver(session)
    writer = CustomerNeo4jWriter(
        cast(AsyncDriver, driver),
        clock=_clock() if clock is None else clock,
    )
    evidence = await writer.prepare_schema(
        batch=current_batch,
        database=_DATABASE,
        transaction_timeout_seconds=2.0,
        operation_timeout_seconds=3.0,
    )
    return evidence, driver, session, current_transaction, writer


@pytest.mark.asyncio
async def test_prepares_schema_in_one_explicit_no_retry_transaction() -> None:
    """Execute schema with one explicit transaction and no retry API."""
    evidence, driver, session, transaction, _ = await _prepare()

    assert driver.session_calls == [
        {
            "database": _DATABASE,
            "default_access_mode": WRITE_ACCESS,
            "fetch_size": 1,
            "disable_auto_commit_retries": True,
        }
    ]
    assert len(session.begin_calls) == 1
    metadata, timeout = session.begin_calls[0]
    assert timeout == 2.0
    assert metadata is not None
    assert metadata["operation"] == "customer-graph-schema"
    assert tuple(query for query, _ in transaction.run_calls) == (
        CUSTOMER_CONSTRAINT_CYPHER,
        CUSTOMER_ACCOUNT_CONSTRAINT_CYPHER,
    )
    assert transaction.commit_calls == 1
    assert transaction.rollback_calls == 0
    assert session.close_calls == 1
    assert driver.close_calls == 0
    assert evidence.committed is True
    assert evidence.bookmarks == ("bookmark-1",)
    assert len(evidence.constraint_writes) == 2


@pytest.mark.asyncio
async def test_writes_nodes_then_relationship_in_one_atomic_transaction() -> None:
    """Write Customer nodes before HAS_ACCOUNT in one transaction."""
    batch = _batch()
    schema_evidence, _, _, _, _ = await _prepare(batch=batch)
    data_transaction = _data_transaction()
    data_session = _FakeSession(data_transaction)
    driver = _FakeDriver(data_session)
    clock = _SequenceClock(
        datetime(2026, 7, 22, 6, 1, tzinfo=UTC),
        datetime(2026, 7, 22, 6, 1, 0, 4000, tzinfo=UTC),
    )
    writer = CustomerNeo4jWriter(cast(AsyncDriver, driver), clock=clock)

    evidence = await writer.write_data(
        batch=batch,
        schema_evidence=schema_evidence,
        database=_DATABASE,
        transaction_timeout_seconds=2.0,
        operation_timeout_seconds=3.0,
    )

    assert tuple(query for query, _ in data_transaction.run_calls) == (
        CUSTOMER_NODE_UPSERT_CYPHER,
        CUSTOMER_ACCOUNT_NODE_UPSERT_CYPHER,
        HAS_ACCOUNT_RELATIONSHIP_UPSERT_CYPHER,
    )
    assert data_transaction.commit_calls == 1
    assert data_transaction.rollback_calls == 0
    assert tuple(item.key for item in evidence.node_writes) == (
        _CUSTOMER_KEY,
        _ACCOUNT_KEY,
    )
    assert evidence.relationship_writes[0].source_key == _CUSTOMER_KEY
    assert evidence.relationship_writes[0].target_key == _ACCOUNT_KEY
    assert evidence.schema_evidence_digest == schema_evidence.evidence_digest
    assert evidence.input_bookmarks == schema_evidence.bookmarks
    assert evidence.output_bookmarks == ("bookmark-1",)
    session_bookmarks = driver.session_calls[0]["bookmarks"]
    assert isinstance(session_bookmarks, Bookmarks)
    assert session_bookmarks.raw_values == frozenset(schema_evidence.bookmarks)


@pytest.mark.asyncio
async def test_data_parameters_are_exact_detached_command_parameters() -> None:
    """Pass only detached parameters produced by fixed commands."""
    batch = _batch()
    schema_evidence, _, _, _, _ = await _prepare(batch=batch)
    transaction = _data_transaction()
    writer = CustomerNeo4jWriter(
        cast(AsyncDriver, _FakeDriver(_FakeSession(transaction))),
        clock=_SequenceClock(
            datetime(2026, 7, 22, 6, 2, tzinfo=UTC),
            datetime(2026, 7, 22, 6, 2, 0, 1000, tzinfo=UTC),
        ),
    )

    await writer.write_data(
        batch=batch,
        schema_evidence=schema_evidence,
        database=_DATABASE,
        transaction_timeout_seconds=2.0,
        operation_timeout_seconds=3.0,
    )

    assert transaction.run_calls[0][1] == (batch.node_commands[0].parameters.to_driver_parameters())
    assert transaction.run_calls[1][1] == (batch.node_commands[1].parameters.to_driver_parameters())
    assert transaction.run_calls[2][1] == (
        batch.relationship_commands[0].parameters.to_driver_parameters()
    )


@pytest.mark.asyncio
async def test_empty_data_batch_still_commits_one_explicit_transaction() -> None:
    """Commit one empty explicit transaction deterministically."""
    batch = _batch(include_data=False)
    schema_evidence, _, _, _, _ = await _prepare(batch=batch)
    transaction = _FakeTransaction([])
    writer = CustomerNeo4jWriter(
        cast(AsyncDriver, _FakeDriver(_FakeSession(transaction))),
        clock=_SequenceClock(
            datetime(2026, 7, 22, 6, 3, tzinfo=UTC),
            datetime(2026, 7, 22, 6, 3, 0, 1000, tzinfo=UTC),
        ),
    )

    evidence = await writer.write_data(
        batch=batch,
        schema_evidence=schema_evidence,
        database=_DATABASE,
        transaction_timeout_seconds=2.0,
        operation_timeout_seconds=3.0,
    )

    assert transaction.run_calls == []
    assert transaction.commit_calls == 1
    assert evidence.node_writes == ()
    assert evidence.relationship_writes == ()


@pytest.mark.asyncio
async def test_rolls_back_on_node_result_mismatch() -> None:
    """Rollback when Neo4j returns a different node identity."""
    batch = _batch()
    schema_evidence, _, _, _, _ = await _prepare(batch=batch)
    transaction = _FakeTransaction([_FakeResult(record={"key": "CUSTOMER_CDM:WRONG"})])
    writer = CustomerNeo4jWriter(
        cast(AsyncDriver, _FakeDriver(_FakeSession(transaction))),
        clock=_clock(),
    )

    with pytest.raises(Neo4jWriteError) as exc_info:
        await writer.write_data(
            batch=batch,
            schema_evidence=schema_evidence,
            database=_DATABASE,
            transaction_timeout_seconds=2.0,
            operation_timeout_seconds=3.0,
        )

    assert exc_info.value.code is Neo4jWriteErrorCode.RESULT_VALUE_INVALID
    assert exc_info.value.phase is Neo4jWritePhase.DATA
    assert transaction.rollback_calls == 1
    assert transaction.commit_calls == 0


@pytest.mark.asyncio
async def test_rolls_back_when_relationship_returns_no_record() -> None:
    """Rollback when required relationship endpoints produce no record."""
    batch = _batch()
    schema_evidence, _, _, _, _ = await _prepare(batch=batch)
    transaction = _FakeTransaction(
        [
            _FakeResult(record={"key": _CUSTOMER_KEY}),
            _FakeResult(record={"key": _ACCOUNT_KEY}),
            _FakeResult(single_error=ResultNotSingleError("zero records")),
        ]
    )
    writer = CustomerNeo4jWriter(
        cast(AsyncDriver, _FakeDriver(_FakeSession(transaction))),
        clock=_clock(),
    )

    with pytest.raises(Neo4jWriteError) as exc_info:
        await writer.write_data(
            batch=batch,
            schema_evidence=schema_evidence,
            database=_DATABASE,
            transaction_timeout_seconds=2.0,
            operation_timeout_seconds=3.0,
        )

    assert exc_info.value.code is (Neo4jWriteErrorCode.RESULT_CARDINALITY_INVALID)
    assert transaction.rollback_calls == 1


@pytest.mark.asyncio
async def test_maps_schema_authentication_failure_without_retry() -> None:
    """Map authentication failure safely without retry."""
    transaction = _FakeTransaction([AuthError("secret")])
    session = _FakeSession(transaction)
    driver = _FakeDriver(session)
    writer = CustomerNeo4jWriter(cast(AsyncDriver, driver), clock=_clock())

    with pytest.raises(Neo4jWriteError) as exc_info:
        await writer.prepare_schema(
            batch=_batch(),
            database=_DATABASE,
            transaction_timeout_seconds=2.0,
            operation_timeout_seconds=3.0,
        )

    assert exc_info.value.code is Neo4jWriteErrorCode.AUTH_FAILED
    assert exc_info.value.safe_message == ("Neo4j authentication or authorization failed.")
    assert len(transaction.run_calls) == 1
    assert transaction.rollback_calls == 1


@pytest.mark.asyncio
async def test_maps_connection_failure_and_rolls_back_data() -> None:
    """Map connection failure and rollback the data transaction."""
    batch = _batch()
    schema_evidence, _, _, _, _ = await _prepare(batch=batch)
    transaction = _FakeTransaction([ServiceUnavailable("internal-host")])
    writer = CustomerNeo4jWriter(
        cast(AsyncDriver, _FakeDriver(_FakeSession(transaction))),
        clock=_clock(),
    )

    with pytest.raises(Neo4jWriteError) as exc_info:
        await writer.write_data(
            batch=batch,
            schema_evidence=schema_evidence,
            database=_DATABASE,
            transaction_timeout_seconds=2.0,
            operation_timeout_seconds=3.0,
        )

    assert exc_info.value.code is Neo4jWriteErrorCode.CONNECTION_FAILED
    assert "internal-host" not in exc_info.value.safe_message
    assert transaction.rollback_calls == 1


@pytest.mark.asyncio
async def test_maps_server_transaction_timeout_code() -> None:
    """Map a server transaction-timeout code."""
    error = _ServerNeo4jError(
        "raw timeout",
        code="Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration",
    )
    transaction = _FakeTransaction([error])
    writer = CustomerNeo4jWriter(
        cast(AsyncDriver, _FakeDriver(_FakeSession(transaction))),
        clock=_clock(),
    )

    with pytest.raises(Neo4jWriteError) as exc_info:
        await writer.prepare_schema(
            batch=_batch(),
            database=_DATABASE,
            transaction_timeout_seconds=2.0,
            operation_timeout_seconds=3.0,
        )

    assert exc_info.value.code is Neo4jWriteErrorCode.TIMEOUT


@pytest.mark.asyncio
async def test_outer_timeout_cancels_transaction() -> None:
    """Bound the whole session operation and cancel in-flight work."""
    wait_event = asyncio.Event()
    transaction = _FakeTransaction([_FakeResult(wait_event=wait_event)])
    session = _FakeSession(transaction)
    writer = CustomerNeo4jWriter(
        cast(AsyncDriver, _FakeDriver(session)),
        clock=_clock(),
    )

    with pytest.raises(Neo4jWriteError) as exc_info:
        await writer.prepare_schema(
            batch=_batch(),
            database=_DATABASE,
            transaction_timeout_seconds=0.05,
            operation_timeout_seconds=0.06,
        )

    assert exc_info.value.code is Neo4jWriteErrorCode.TIMEOUT
    assert transaction.cancel_calls == 1
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_external_cancellation_is_preserved_and_cancels_session() -> None:
    """Preserve caller cancellation and cancel Neo4j work."""
    wait_event = asyncio.Event()
    transaction = _FakeTransaction([_FakeResult(wait_event=wait_event)])
    session = _FakeSession(transaction)
    writer = CustomerNeo4jWriter(
        cast(AsyncDriver, _FakeDriver(session)),
        clock=_clock(),
    )
    task = asyncio.create_task(
        writer.prepare_schema(
            batch=_batch(),
            database=_DATABASE,
            transaction_timeout_seconds=2.0,
            operation_timeout_seconds=3.0,
        )
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert transaction.cancel_calls == 1
    assert session.cancel_calls == 1


@pytest.mark.asyncio
async def test_incomplete_commit_is_outcome_unknown_and_not_rolled_back() -> None:
    """Do not retry or rollback an unknown commit outcome."""
    transaction = _FakeTransaction(
        [_FakeResult(), _FakeResult()],
        commit_error=IncompleteCommit("lost commit response"),
    )
    writer = CustomerNeo4jWriter(
        cast(AsyncDriver, _FakeDriver(_FakeSession(transaction))),
        clock=_clock(),
    )

    with pytest.raises(Neo4jWriteError) as exc_info:
        await writer.prepare_schema(
            batch=_batch(),
            database=_DATABASE,
            transaction_timeout_seconds=2.0,
            operation_timeout_seconds=3.0,
        )

    assert exc_info.value.code is Neo4jWriteErrorCode.COMMIT_OUTCOME_UNKNOWN
    assert transaction.commit_calls == 1
    assert transaction.rollback_calls == 0
    assert len(transaction.run_calls) == 2


@pytest.mark.asyncio
async def test_rollback_failure_is_explicit() -> None:
    """Expose rollback failure with a stable code."""
    transaction = _FakeTransaction(
        [
            _ServerNeo4jError(
                "query failure",
                code="Neo.ClientError.Statement.SyntaxError",
            )
        ],
        rollback_error=ServiceUnavailable("rollback failed"),
    )
    writer = CustomerNeo4jWriter(
        cast(AsyncDriver, _FakeDriver(_FakeSession(transaction))),
        clock=_clock(),
    )

    with pytest.raises(Neo4jWriteError) as exc_info:
        await writer.prepare_schema(
            batch=_batch(),
            database=_DATABASE,
            transaction_timeout_seconds=2.0,
            operation_timeout_seconds=3.0,
        )

    assert exc_info.value.code is Neo4jWriteErrorCode.ROLLBACK_FAILED


@pytest.mark.asyncio
async def test_requires_schema_bookmark_after_committed_transaction() -> None:
    """Fail closed when Neo4j returns no causal bookmark."""
    transaction = _schema_transaction()
    session = _FakeSession(transaction, bookmark_values=())
    writer = CustomerNeo4jWriter(
        cast(AsyncDriver, _FakeDriver(session)),
        clock=_clock(),
    )

    with pytest.raises(Neo4jWriteError) as exc_info:
        await writer.prepare_schema(
            batch=_batch(),
            database=_DATABASE,
            transaction_timeout_seconds=2.0,
            operation_timeout_seconds=3.0,
        )

    assert exc_info.value.code is Neo4jWriteErrorCode.BOOKMARK_MISSING
    assert transaction.commit_calls == 1
    assert transaction.rollback_calls == 0


@pytest.mark.asyncio
async def test_maps_closed_driver_session_creation_failure() -> None:
    """Map driver session creation failure before any transaction starts."""
    driver = _FakeDriver(session_error=ServiceUnavailable("driver closed"))
    writer = CustomerNeo4jWriter(cast(AsyncDriver, driver), clock=_clock())

    with pytest.raises(Neo4jWriteError) as exc_info:
        await writer.prepare_schema(
            batch=_batch(),
            database=_DATABASE,
            transaction_timeout_seconds=2.0,
            operation_timeout_seconds=3.0,
        )

    assert exc_info.value.code is Neo4jWriteErrorCode.CONNECTION_FAILED
    assert len(driver.session_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("database", ["", " neo4j", "bad.name", "bad\x00name"])
async def test_rejects_invalid_database_before_driver_access(database: str) -> None:
    """Reject unsafe database names before driver access."""
    driver = _FakeDriver()
    writer = CustomerNeo4jWriter(cast(AsyncDriver, driver), clock=_clock())

    with pytest.raises(Neo4jWriteError) as exc_info:
        await writer.prepare_schema(
            batch=_batch(),
            database=database,
            transaction_timeout_seconds=2.0,
            operation_timeout_seconds=3.0,
        )

    assert exc_info.value.code is Neo4jWriteErrorCode.DATABASE_INVALID
    assert driver.session_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transaction_timeout", "operation_timeout"),
    [
        (0.0, 1.0),
        (True, 1.0),
        (1, 2.0),
        (float("nan"), 2.0),
        (2.0, 1.0),
        (2.0, 2.0),
        (2.0, float("inf")),
        (301.0, 400.0),
        (2.0, 601.0),
    ],
)
async def test_rejects_invalid_timeouts_before_driver_access(
    transaction_timeout: object,
    operation_timeout: object,
) -> None:
    """Reject invalid bounded timeouts before driver access."""
    driver = _FakeDriver()
    writer = CustomerNeo4jWriter(cast(AsyncDriver, driver), clock=_clock())

    with pytest.raises(Neo4jWriteError) as exc_info:
        await writer.prepare_schema(
            batch=_batch(),
            database=_DATABASE,
            transaction_timeout_seconds=cast(float, transaction_timeout),
            operation_timeout_seconds=cast(float, operation_timeout),
        )

    assert exc_info.value.code is Neo4jWriteErrorCode.TIMEOUT_INVALID
    assert driver.session_calls == []


@pytest.mark.asyncio
async def test_rejects_corrupted_batch_before_driver_access() -> None:
    """Revalidate a corrupted command batch before driver access."""
    current = _batch()
    corrupted = current.model_copy(update={"command_batch_digest": "b" * 64})
    driver = _FakeDriver()
    writer = CustomerNeo4jWriter(cast(AsyncDriver, driver), clock=_clock())

    with pytest.raises(Neo4jWriteError) as exc_info:
        await writer.prepare_schema(
            batch=corrupted,
            database=_DATABASE,
            transaction_timeout_seconds=2.0,
            operation_timeout_seconds=3.0,
        )

    assert exc_info.value.code is Neo4jWriteErrorCode.COMMAND_BATCH_INVALID
    assert driver.session_calls == []


@pytest.mark.asyncio
async def test_data_requires_matching_revalidated_schema_evidence() -> None:
    """Require matching untampered schema evidence before data writes."""
    batch = _batch()
    schema_evidence, _, _, _, _ = await _prepare(batch=batch)
    mutable = cast(
        _MutableSchemaEvidence,
        schema_evidence.model_copy(update={"database": "other"}),
    )
    driver = _FakeDriver()
    writer = CustomerNeo4jWriter(cast(AsyncDriver, driver), clock=_clock())

    with pytest.raises(Neo4jWriteError) as exc_info:
        await writer.write_data(
            batch=batch,
            schema_evidence=cast(Neo4jSchemaPreparationEvidence, mutable),
            database=_DATABASE,
            transaction_timeout_seconds=2.0,
            operation_timeout_seconds=3.0,
        )

    assert exc_info.value.code is (Neo4jWriteErrorCode.SCHEMA_EVIDENCE_MISMATCH)
    assert driver.session_calls == []


@pytest.mark.asyncio
async def test_evidence_models_are_frozen_and_digest_bound() -> None:
    """Keep execution evidence frozen and digest-bound."""
    batch = _batch()
    transaction = _schema_transaction()
    writer = CustomerNeo4jWriter(
        cast(AsyncDriver, _FakeDriver(_FakeSession(transaction))),
        clock=_clock(),
    )
    evidence = await writer.prepare_schema(
        batch=batch,
        database=_DATABASE,
        transaction_timeout_seconds=2.0,
        operation_timeout_seconds=3.0,
    )

    mutable_evidence = cast(_MutableSchemaEvidence, evidence)
    with pytest.raises(ValidationError):
        mutable_evidence.database = "other"

    with pytest.raises(ValidationError):
        Neo4jSchemaPreparationEvidence.model_validate(
            {
                **evidence.model_dump(mode="python"),
                "evidence_digest": "b" * 64,
            }
        )


def test_writer_source_contains_no_managed_retry_or_driver_close() -> None:
    """Reject accidental execute_write/execute_query and driver ownership."""
    source_path = Path(__file__).parents[1] / "src/return_platform/data_platform/graph/writer.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

    assert "execute_write" not in attributes
    assert "execute_query" not in attributes
    assert "execute_read" not in attributes
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "close"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "_driver"
        for node in ast.walk(tree)
    )

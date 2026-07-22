"""Adversarial tests for the read-only Customer MongoDB source adapter."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from bson import BSON
from pymongo.errors import (
    AutoReconnect,
    ExecutionTimeout,
    OperationFailure,
    ServerSelectionTimeoutError,
)

from return_platform.data_platform.mapping import (
    CompiledSourceAssetPlan,
    LoadedDataPlatformConfiguration,
    MappingExecutionPlan,
    build_customer_account_canonical_model_registry,
    build_customer_account_handler_registry,
    compile_customer_profile_mapping,
    load_data_platform_mapping_configuration,
)
from return_platform.data_platform.mapping.contracts import (
    SourceAssetDefinition,
    SourceLifecycle,
)
from return_platform.data_platform.sources.mongodb import (
    CUSTOMER_FIND_COMMENT,
    CustomerMongoSourceAdapter,
    CustomerMongoSourceError,
    CustomerMongoSourceErrorCode,
)
from return_platform.shared.governance import (
    AllowedOperation,
    AssetCatalog,
    AssetCatalogEntry,
    DataStoreType,
    ObjectKind,
    OwnershipClass,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pymongo import AsyncMongoClient


_CONFIG_DIR = Path(__file__).parents[1] / "config" / "data_platform"
_OBSERVED_AT = datetime(2026, 7, 21, 8, 30, tzinfo=UTC)
_SOURCE_ID = "PARTY-100"


class _FakeCollection:
    """Controllable asynchronous find_one implementation."""

    def __init__(
        self,
        *,
        result: dict[str, object] | None = None,
        error: BaseException | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        self.result = result
        self.error = error
        self.delay_seconds = delay_seconds
        self.calls: list[tuple[object | None, tuple[object, ...], dict[str, object]]] = []
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def find_one(
        self,
        query_filter: object | None = None,
        *args: object,
        **kwargs: object,
    ) -> dict[str, object] | None:
        """Record the exact query and return the configured outcome."""
        self.calls.append((query_filter, args, kwargs))
        self.started.set()
        try:
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            if self.error is not None:
                raise self.error
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        else:
            return self.result


class _FakeDatabase:
    """Exact-name collection resolver."""

    def __init__(self, collection: _FakeCollection) -> None:
        self.collection = collection
        self.requested_collections: list[str] = []

    def __getitem__(self, name: str) -> _FakeCollection:
        """Resolve one collection without enumeration."""
        self.requested_collections.append(name)
        return self.collection


class _FakeClient:
    """Exact-name database resolver with client-ownership evidence."""

    def __init__(self, database: _FakeDatabase) -> None:
        self.database = database
        self.requested_databases: list[str] = []
        self.close_calls = 0

    def __getitem__(self, name: str) -> _FakeDatabase:
        """Resolve one database without enumeration."""
        self.requested_databases.append(name)
        return self.database

    async def close(self) -> None:
        """Record an ownership violation if the adapter closes this client."""
        self.close_calls += 1


def _loaded() -> LoadedDataPlatformConfiguration:
    """Load the approved Customer profile."""
    return load_data_platform_mapping_configuration(_CONFIG_DIR)


def _asset(
    *,
    database: str = "eventMessages",
    object_name: str = "customerOutboundCDM",
) -> AssetCatalogEntry:
    """Create one governed Customer CDM collection fixture."""
    return AssetCatalogEntry(
        asset_id="source.mongodb.customer_outbound_cdm",
        store=DataStoreType.MONGODB,
        database=database,
        namespace=None,
        object_name=object_name,
        object_kind=ObjectKind.COLLECTION,
        ownership=OwnershipClass.SOURCE_SYSTEM,
        authoritative=True,
        allowed_operations=(AllowedOperation.READ,),
    )


def _plan(*, asset: AssetCatalogEntry | None = None) -> MappingExecutionPlan:
    """Compile the approved Customer mapping plan."""
    selected_asset = asset or _asset()
    return compile_customer_profile_mapping(
        _loaded(),
        AssetCatalog(version="1.0", assets=(selected_asset,)),
        build_customer_account_handler_registry(),
        build_customer_account_canonical_model_registry(),
    )


def _source_plan(
    *,
    asset: AssetCatalogEntry | None = None,
) -> CompiledSourceAssetPlan:
    """Return the one compiled Customer source plan."""
    return _plan(asset=asset).sources[0]


def _document() -> dict[str, object]:
    """Create one BSON-valid Customer source document."""
    return {
        "_id": _SOURCE_ID,
        "partyId": _SOURCE_ID,
        "custAccts": [{"accountNumber": "202*100"}],
    }


def _adapter(
    collection: _FakeCollection,
    *,
    timeout_seconds: float = 1.0,
) -> tuple[CustomerMongoSourceAdapter, _FakeClient, _FakeDatabase]:
    """Create one adapter around an injected fake client."""
    database = _FakeDatabase(collection)
    client = _FakeClient(database)
    adapter = CustomerMongoSourceAdapter(
        cast("AsyncMongoClient[dict[str, object]]", client),
        operation_timeout_seconds=timeout_seconds,
    )
    return adapter, client, database


@pytest.mark.asyncio
async def test_fetches_one_exact_id_from_compiled_database_and_collection() -> None:
    """Use only the governed source plan and one exact `_id` filter."""
    document = _document()
    collection = _FakeCollection(result=document)
    adapter, client, database = _adapter(collection, timeout_seconds=1.25)

    result = await adapter.fetch_by_id(
        source_plan=_source_plan(),
        source_document_id=_SOURCE_ID,
        observed_at=_OBSERVED_AT,
    )

    assert client.requested_databases == ["eventMessages"]
    assert database.requested_collections == ["customerOutboundCDM"]
    assert collection.calls == [
        (
            {"_id": _SOURCE_ID},
            (),
            {"max_time_ms": 1250, "comment": CUSTOMER_FIND_COMMENT},
        )
    ]
    assert client.close_calls == 0
    assert result.source_id == "source.customer_cdm.v1"
    assert result.catalog_asset_id == "source.mongodb.customer_outbound_cdm"
    assert result.database == "eventMessages"
    assert result.collection == "customerOutboundCDM"
    assert result.evidence.source_document_id == _SOURCE_ID
    assert result.evidence.observed_at == _OBSERVED_AT
    assert result.evidence.source_hash == hashlib.sha256(BSON.encode(document)).hexdigest()


@pytest.mark.asyncio
async def test_returns_detached_recursively_immutable_document() -> None:
    """Prevent caller mutation from changing fetched source evidence."""
    document = _document()
    collection = _FakeCollection(result=document)
    adapter, _, _ = _adapter(collection)

    result = await adapter.fetch_by_id(
        source_plan=_source_plan(),
        source_document_id=_SOURCE_ID,
        observed_at=_OBSERVED_AT,
    )

    document["partyId"] = "CHANGED"
    accounts = cast("list[dict[str, object]]", document["custAccts"])
    accounts[0]["accountNumber"] = "999*999"

    assert result.document["partyId"] == _SOURCE_ID
    frozen_accounts = result.document["custAccts"]
    assert isinstance(frozen_accounts, tuple)
    frozen_account = cast("Mapping[str, object]", frozen_accounts[0])
    assert frozen_account["accountNumber"] == "202*100"
    with pytest.raises(TypeError):
        cast("dict[str, object]", result.document)["partyId"] = "INVALID"
    with pytest.raises(TypeError):
        cast("dict[str, object]", frozen_account)["accountNumber"] = "INVALID"


@pytest.mark.asyncio
async def test_normalizes_observation_timestamp_to_utc() -> None:
    """Normalize an aware non-UTC observation timestamp."""
    offset = timezone(timedelta(hours=5, minutes=30))
    adapter, _, _ = _adapter(_FakeCollection(result=_document()))

    result = await adapter.fetch_by_id(
        source_plan=_source_plan(),
        source_document_id=_SOURCE_ID,
        observed_at=datetime(2026, 7, 21, 14, 0, tzinfo=offset),
    )

    assert result.evidence.observed_at == datetime(2026, 7, 21, 8, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_missing_document_raises_safe_not_found() -> None:
    """Treat an absent exact `_id` as an explicit bounded failure."""
    adapter, _, _ = _adapter(_FakeCollection(result=None))

    with pytest.raises(CustomerMongoSourceError) as exc_info:
        await adapter.fetch_by_id(
            source_plan=_source_plan(),
            source_document_id=_SOURCE_ID,
            observed_at=_OBSERVED_AT,
        )

    assert exc_info.value.code is CustomerMongoSourceErrorCode.SOURCE_DOCUMENT_NOT_FOUND


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (
            OperationFailure("authentication failed", code=18),
            CustomerMongoSourceErrorCode.AUTH_FAILED,
        ),
        (
            OperationFailure("not authorized", code=13),
            CustomerMongoSourceErrorCode.AUTH_FAILED,
        ),
        (
            ServerSelectionTimeoutError("server selection timed out"),
            CustomerMongoSourceErrorCode.TIMEOUT,
        ),
        (
            ExecutionTimeout("execution timed out", code=50),
            CustomerMongoSourceErrorCode.TIMEOUT,
        ),
        (
            AutoReconnect("connection dropped"),
            CustomerMongoSourceErrorCode.CONNECTION_FAILED,
        ),
        (
            OperationFailure("query failed", code=2),
            CustomerMongoSourceErrorCode.QUERY_FAILED,
        ),
    ],
)
async def test_maps_pymongo_errors_without_leaking_driver_messages(
    error: BaseException,
    expected_code: CustomerMongoSourceErrorCode,
) -> None:
    """Classify driver failures into stable safe public codes."""
    adapter, _, _ = _adapter(_FakeCollection(error=error))

    with pytest.raises(CustomerMongoSourceError) as exc_info:
        await adapter.fetch_by_id(
            source_plan=_source_plan(),
            source_document_id=_SOURCE_ID,
            observed_at=_OBSERVED_AT,
        )

    assert exc_info.value.code is expected_code
    assert "authentication failed" not in exc_info.value.safe_message
    assert "server selection" not in exc_info.value.safe_message
    assert "connection dropped" not in exc_info.value.safe_message


@pytest.mark.asyncio
async def test_enforces_outer_asyncio_timeout_and_cancels_driver_operation() -> None:
    """Bound network selection and read latency beyond server execution time."""
    collection = _FakeCollection(result=_document(), delay_seconds=1.0)
    adapter, _, _ = _adapter(collection, timeout_seconds=0.05)

    with pytest.raises(CustomerMongoSourceError) as exc_info:
        await adapter.fetch_by_id(
            source_plan=_source_plan(),
            source_document_id=_SOURCE_ID,
            observed_at=_OBSERVED_AT,
        )

    assert exc_info.value.code is CustomerMongoSourceErrorCode.TIMEOUT
    assert collection.cancelled.is_set()


@pytest.mark.asyncio
async def test_preserves_external_cancellation() -> None:
    """Propagate caller cancellation rather than mapping it to timeout."""
    collection = _FakeCollection(result=_document(), delay_seconds=10.0)
    adapter, _, _ = _adapter(collection, timeout_seconds=30.0)
    task = asyncio.create_task(
        adapter.fetch_by_id(
            source_plan=_source_plan(),
            source_document_id=_SOURCE_ID,
            observed_at=_OBSERVED_AT,
        )
    )
    await collection.started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert collection.cancelled.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "document",
    [
        {"_id": "OTHER", "partyId": _SOURCE_ID},
        {"_id": 100, "partyId": _SOURCE_ID},
        {"partyId": _SOURCE_ID},
        {"_id": _SOURCE_ID, "unsupported": object()},
    ],
)
async def test_rejects_invalid_returned_documents(document: dict[str, object]) -> None:
    """Reject mismatched identities and values that cannot represent BSON."""
    adapter, _, _ = _adapter(_FakeCollection(result=document))

    with pytest.raises(CustomerMongoSourceError) as exc_info:
        await adapter.fetch_by_id(
            source_plan=_source_plan(),
            source_document_id=_SOURCE_ID,
            observed_at=_OBSERVED_AT,
        )

    assert exc_info.value.code is CustomerMongoSourceErrorCode.SOURCE_DOCUMENT_INVALID


@pytest.mark.asyncio
async def test_rejects_cyclic_returned_document() -> None:
    """Fail closed on a malformed cyclic fake-driver document."""
    document = _document()
    document["cycle"] = document
    adapter, _, _ = _adapter(_FakeCollection(result=document))

    with pytest.raises(CustomerMongoSourceError) as exc_info:
        await adapter.fetch_by_id(
            source_plan=_source_plan(),
            source_document_id=_SOURCE_ID,
            observed_at=_OBSERVED_AT,
        )

    assert exc_info.value.code is CustomerMongoSourceErrorCode.SOURCE_DOCUMENT_INVALID


@pytest.mark.asyncio
async def test_rejects_non_string_top_level_key() -> None:
    """Reject impossible non-string BSON document keys from a bad client."""
    document = cast("dict[str, object]", {"_id": _SOURCE_ID, 1: "invalid"})
    adapter, _, _ = _adapter(_FakeCollection(result=document))

    with pytest.raises(CustomerMongoSourceError) as exc_info:
        await adapter.fetch_by_id(
            source_plan=_source_plan(),
            source_document_id=_SOURCE_ID,
            observed_at=_OBSERVED_AT,
        )

    assert exc_info.value.code is CustomerMongoSourceErrorCode.SOURCE_DOCUMENT_INVALID


@pytest.mark.asyncio
async def test_rejects_wrong_source_system_plan() -> None:
    """Defend against a compiled source plan routed to the wrong adapter."""
    current = _source_plan()
    wrong_definition = SourceAssetDefinition(
        source_id=current.definition.source_id,
        source_system="TDS",
        catalog_asset_id=current.definition.catalog_asset_id,
        lifecycle=SourceLifecycle.ACTIVE,
        required_for_sync=True,
    )
    wrong_plan = replace(current, definition=wrong_definition)
    adapter, _, _ = _adapter(_FakeCollection(result=_document()))

    with pytest.raises(CustomerMongoSourceError) as exc_info:
        await adapter.fetch_by_id(
            source_plan=wrong_plan,
            source_document_id=_SOURCE_ID,
            observed_at=_OBSERVED_AT,
        )

    assert exc_info.value.code is CustomerMongoSourceErrorCode.SOURCE_PLAN_INVALID


@pytest.mark.asyncio
async def test_revalidates_corrupted_compiled_namespace_before_client_access() -> None:
    """Reject a corrupted upstream plan without touching the injected client."""
    current = _source_plan()
    invalid_asset = current.catalog_asset.model_copy(update={"object_name": "bad\x00collection"})
    invalid_plan = replace(current, catalog_asset=invalid_asset)
    adapter, client, database = _adapter(_FakeCollection(result=_document()))

    with pytest.raises(CustomerMongoSourceError) as exc_info:
        await adapter.fetch_by_id(
            source_plan=invalid_plan,
            source_document_id=_SOURCE_ID,
            observed_at=_OBSERVED_AT,
        )

    assert exc_info.value.code is CustomerMongoSourceErrorCode.SOURCE_PLAN_INVALID
    assert client.requested_databases == []
    assert database.requested_collections == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_document_id", "observed_at"),
    [
        (123, _OBSERVED_AT),
        ("", _OBSERVED_AT),
        ("BAD ID", _OBSERVED_AT),
        (_SOURCE_ID, _OBSERVED_AT.replace(tzinfo=None)),
        (_SOURCE_ID, "2026-07-21T08:30:00Z"),
    ],
)
async def test_rejects_invalid_fetch_inputs_before_client_access(
    source_document_id: object,
    observed_at: object,
) -> None:
    """Validate strict identifiers and aware datetime inputs before I/O."""
    adapter, client, _ = _adapter(_FakeCollection(result=_document()))

    with pytest.raises(CustomerMongoSourceError) as exc_info:
        await adapter.fetch_by_id(
            source_plan=_source_plan(),
            source_document_id=cast("str", source_document_id),
            observed_at=cast("datetime", observed_at),
        )

    assert exc_info.value.code is CustomerMongoSourceErrorCode.INVALID_INPUT
    assert client.requested_databases == []


@pytest.mark.parametrize(
    "timeout_seconds",
    [True, 0.0, 0.049, 30.001, float("inf"), float("nan")],
)
def test_rejects_invalid_timeout_configuration(timeout_seconds: object) -> None:
    """Reject Boolean, non-finite, and out-of-bound operation timeouts."""
    client = _FakeClient(_FakeDatabase(_FakeCollection(result=_document())))

    with pytest.raises(CustomerMongoSourceError) as exc_info:
        CustomerMongoSourceAdapter(
            cast("AsyncMongoClient[dict[str, object]]", client),
            operation_timeout_seconds=cast("float", timeout_seconds),
        )

    assert exc_info.value.code is CustomerMongoSourceErrorCode.INVALID_INPUT

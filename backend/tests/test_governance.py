"""Tests for strict data-governance contracts."""

import pytest
from pydantic import ValidationError

from return_platform.shared.governance import (
    AllowedOperation,
    AssetCatalog,
    AssetCatalogEntry,
    DataStoreType,
    ObjectKind,
    OwnershipClass,
    SamplingConfig,
)


def create_source_asset() -> AssetCatalogEntry:
    """Create a valid read-only SQL Server source asset."""

    return AssetCatalogEntry(
        asset_id="source.sqlserver.sales_orders",
        store=DataStoreType.SQLSERVER,
        database="return_platform",
        namespace="dbo",
        object_name="sales_orders",
        object_kind=ObjectKind.TABLE,
        ownership=OwnershipClass.SOURCE_SYSTEM,
        authoritative=True,
        allowed_operations=(AllowedOperation.READ,),
    )


def create_platform_asset() -> AssetCatalogEntry:
    """Create a valid MongoDB platform-owned asset."""

    return AssetCatalogEntry(
        asset_id="platform.mongodb.return_sessions",
        store=DataStoreType.MONGODB,
        database="return_platform",
        object_name="return_sessions",
        object_kind=ObjectKind.COLLECTION,
        ownership=OwnershipClass.PLATFORM_OWNED,
        authoritative=True,
        allowed_operations=(
            AllowedOperation.READ,
            AllowedOperation.WRITE,
        ),
    )


def test_empty_catalog_is_valid() -> None:
    catalog = AssetCatalog(
        version="1.0",
        assets=(),
    )

    assert catalog.version == "1.0"
    assert catalog.assets == ()


def test_valid_catalog_entries_are_accepted() -> None:
    catalog = AssetCatalog(
        version="1.0",
        assets=(
            create_source_asset(),
            create_platform_asset(),
        ),
    )

    assert len(catalog.assets) == 2


def test_source_system_asset_is_strictly_read_only() -> None:
    with pytest.raises(
        ValidationError,
        match="SOURCE_SYSTEM assets are strictly read-only",
    ):
        AssetCatalogEntry(
            asset_id="source.sqlserver.sales_orders",
            store=DataStoreType.SQLSERVER,
            database="return_platform",
            namespace="dbo",
            object_name="sales_orders",
            object_kind=ObjectKind.TABLE,
            ownership=OwnershipClass.SOURCE_SYSTEM,
            authoritative=True,
            allowed_operations=(
                AllowedOperation.READ,
                AllowedOperation.WRITE,
            ),
        )


def test_sqlserver_asset_requires_namespace() -> None:
    with pytest.raises(
        ValidationError,
        match="must declare a schema namespace",
    ):
        AssetCatalogEntry(
            asset_id="source.sqlserver.sales_orders",
            store=DataStoreType.SQLSERVER,
            database="return_platform",
            object_name="sales_orders",
            object_kind=ObjectKind.TABLE,
            ownership=OwnershipClass.SOURCE_SYSTEM,
            authoritative=True,
            allowed_operations=(AllowedOperation.READ,),
        )


def test_sqlserver_rejects_collection_kind() -> None:
    with pytest.raises(
        ValidationError,
        match="must be TABLE or VIEW",
    ):
        AssetCatalogEntry(
            asset_id="source.sqlserver.sales_orders",
            store=DataStoreType.SQLSERVER,
            database="return_platform",
            namespace="dbo",
            object_name="sales_orders",
            object_kind=ObjectKind.COLLECTION,
            ownership=OwnershipClass.SOURCE_SYSTEM,
            authoritative=True,
            allowed_operations=(AllowedOperation.READ,),
        )


def test_mongodb_asset_rejects_namespace() -> None:
    with pytest.raises(
        ValidationError,
        match="must not declare a schema namespace",
    ):
        AssetCatalogEntry(
            asset_id="platform.mongodb.return_sessions",
            store=DataStoreType.MONGODB,
            database="return_platform",
            namespace="return_sessions",
            object_name="return_sessions",
            object_kind=ObjectKind.COLLECTION,
            ownership=OwnershipClass.PLATFORM_OWNED,
            authoritative=True,
            allowed_operations=(AllowedOperation.READ,),
        )


def test_mongodb_rejects_table_kind() -> None:
    with pytest.raises(
        ValidationError,
        match="must use object_kind COLLECTION",
    ):
        AssetCatalogEntry(
            asset_id="platform.mongodb.return_sessions",
            store=DataStoreType.MONGODB,
            database="return_platform",
            object_name="return_sessions",
            object_kind=ObjectKind.TABLE,
            ownership=OwnershipClass.PLATFORM_OWNED,
            authoritative=True,
            allowed_operations=(AllowedOperation.READ,),
        )


def test_derived_projection_cannot_be_authoritative() -> None:
    with pytest.raises(
        ValidationError,
        match="cannot be authoritative",
    ):
        AssetCatalogEntry(
            asset_id="derived.mongodb.order_projection",
            store=DataStoreType.MONGODB,
            database="return_platform",
            object_name="order_projection",
            object_kind=ObjectKind.COLLECTION,
            ownership=OwnershipClass.DERIVED_PROJECTION,
            authoritative=True,
            allowed_operations=(
                AllowedOperation.READ,
                AllowedOperation.REBUILD,
            ),
        )


def test_enabled_sampling_requires_positive_limit() -> None:
    with pytest.raises(
        ValidationError,
        match="max_rows must be between 1 and 25",
    ):
        SamplingConfig(
            enabled=True,
            max_rows=0,
        )


def test_disabled_sampling_requires_zero_limit() -> None:
    with pytest.raises(
        ValidationError,
        match="max_rows must be 0",
    ):
        SamplingConfig(
            enabled=False,
            max_rows=25,
        )


def test_duplicate_redaction_fields_are_rejected() -> None:
    with pytest.raises(
        ValidationError,
        match="must not contain duplicates",
    ):
        SamplingConfig(
            enabled=True,
            max_rows=10,
            redact_fields=(
                "customer.email",
                "customer.email",
            ),
        )


def test_sampling_requires_read_permission() -> None:
    with pytest.raises(
        ValidationError,
        match="sampling requires the READ operation",
    ):
        AssetCatalogEntry(
            asset_id="platform.mongodb.write_only_asset",
            store=DataStoreType.MONGODB,
            database="return_platform",
            object_name="write_only_asset",
            object_kind=ObjectKind.COLLECTION,
            ownership=OwnershipClass.PLATFORM_OWNED,
            authoritative=True,
            allowed_operations=(AllowedOperation.WRITE,),
            sampling=SamplingConfig(
                enabled=True,
                max_rows=10,
            ),
        )


def test_duplicate_asset_ids_are_rejected() -> None:
    asset = create_source_asset()

    with pytest.raises(
        ValidationError,
        match="duplicate asset_id",
    ):
        AssetCatalog(
            version="1.0",
            assets=(asset, asset),
        )


def test_duplicate_physical_assets_are_rejected() -> None:
    first_asset = create_source_asset()

    second_asset = AssetCatalogEntry(
        asset_id="source.sqlserver.duplicate_sales_orders",
        store=DataStoreType.SQLSERVER,
        database="return_platform",
        namespace="dbo",
        object_name="sales_orders",
        object_kind=ObjectKind.TABLE,
        ownership=OwnershipClass.SOURCE_SYSTEM,
        authoritative=True,
        allowed_operations=(AllowedOperation.READ,),
    )

    with pytest.raises(
        ValidationError,
        match="same physical asset",
    ):
        AssetCatalog(
            version="1.0",
            assets=(
                first_asset,
                second_asset,
            ),
        )


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(
        ValidationError,
        match="Extra inputs are not permitted",
    ):
        AssetCatalogEntry.model_validate(
            {
                "asset_id": "source.sqlserver.sales_orders",
                "store": "SQLSERVER",
                "database": "return_platform",
                "namespace": "dbo",
                "object_name": "sales_orders",
                "object_kind": "TABLE",
                "ownership": "SOURCE_SYSTEM",
                "authoritative": True,
                "allowed_operations": ["READ"],
                "unknown_setting": "not-allowed",
            }
        )


def test_catalog_is_immutable() -> None:
    catalog = AssetCatalog()
    field_name = "assets"
    with pytest.raises(
        ValidationError,
        match="Instance is frozen",
    ):

        setattr(
            catalog,
           field_name,
            (create_source_asset(),),
        )

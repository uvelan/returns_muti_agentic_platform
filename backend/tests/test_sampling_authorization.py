"""Tests for catalog-backed sampling authorization."""

import pytest

from return_platform.data_governance.sampling.authorization import (
    SamplingAuthorizationCode,
    SamplingAuthorizationError,
    authorize_sampling_asset,
)
from return_platform.data_governance.sampling.contracts import (
    MAX_SAMPLE_ROWS,
)
from return_platform.shared.governance import (
    AllowedOperation,
    AssetCatalog,
    AssetCatalogEntry,
    DataStoreType,
    ObjectKind,
    OwnershipClass,
    SamplingConfig,
)


def _enabled_sampling(
    *,
    max_rows: int = 5,
    redact_fields: tuple[str, ...] = (
        "email",
        "phone",
    ),
) -> SamplingConfig:
    """Create a valid enabled sampling policy."""

    return SamplingConfig(
        enabled=True,
        max_rows=max_rows,
        redact_fields=redact_fields,
    )


def _disabled_sampling() -> SamplingConfig:
    """Create an explicitly disabled sampling policy."""

    return SamplingConfig(
        enabled=False,
        max_rows=0,
        redact_fields=(),
    )


def _sqlserver_asset(
    *,
    asset_id: str = "source.sqlserver.users",
    sampling: SamplingConfig | None = None,
) -> AssetCatalogEntry:
    """Create a cataloged SQL Server table."""

    return AssetCatalogEntry(
        asset_id=asset_id,
        store=DataStoreType.SQLSERVER,
        database="return_platform",
        namespace="dbo",
        object_name="users",
        object_kind=ObjectKind.TABLE,
        ownership=OwnershipClass.SOURCE_SYSTEM,
        authoritative=True,
        allowed_operations=(AllowedOperation.READ,),
        sampling=(sampling if sampling is not None else _enabled_sampling()),
    )


def _mongodb_asset(
    *,
    asset_id: str = "source.mongodb.sessions",
    sampling: SamplingConfig | None = None,
) -> AssetCatalogEntry:
    """Create a cataloged MongoDB collection."""

    return AssetCatalogEntry(
        asset_id=asset_id,
        store=DataStoreType.MONGODB,
        database="return_platform",
        namespace=None,
        object_name="sessions",
        object_kind=ObjectKind.COLLECTION,
        ownership=OwnershipClass.SOURCE_SYSTEM,
        authoritative=True,
        allowed_operations=(AllowedOperation.READ,),
        sampling=(sampling if sampling is not None else _enabled_sampling()),
    )


def _catalog(
    *assets: AssetCatalogEntry,
) -> AssetCatalog:
    """Create a valid immutable asset catalog."""

    return AssetCatalog(
        version="1.0",
        assets=assets,
    )


def _unsafe_catalog(
    *assets: AssetCatalogEntry,
) -> AssetCatalog:
    """Construct malformed catalog input for defensive-boundary tests."""

    return AssetCatalog.model_construct(
        version="1.0",
        assets=assets,
    )


def test_authorizes_sqlserver_asset_from_catalog() -> None:
    """Return the exact SQL Server entry stored in the catalog."""

    catalog_asset = _sqlserver_asset()
    catalog = _catalog(
        catalog_asset,
    )

    authorized_asset = authorize_sampling_asset(
        catalog=catalog,
        asset_id="source.sqlserver.users",
        expected_store=DataStoreType.SQLSERVER,
    )

    assert authorized_asset is catalog_asset
    assert authorized_asset.sampling.enabled is True
    assert authorized_asset.sampling.max_rows == 5


def test_authorizes_mongodb_asset_from_catalog() -> None:
    """Return the exact MongoDB entry stored in the catalog."""

    catalog_asset = _mongodb_asset()
    catalog = _catalog(
        catalog_asset,
    )

    authorized_asset = authorize_sampling_asset(
        catalog=catalog,
        asset_id="source.mongodb.sessions",
        expected_store=DataStoreType.MONGODB,
    )

    assert authorized_asset is catalog_asset
    assert authorized_asset.object_kind == ObjectKind.COLLECTION
    assert authorized_asset.namespace is None


def test_unknown_asset_is_rejected() -> None:
    """Reject asset IDs that are absent from the approved catalog."""

    catalog = _catalog(
        _sqlserver_asset(),
    )

    with pytest.raises(
        SamplingAuthorizationError,
        match="not present in the catalog",
    ) as error_info:
        authorize_sampling_asset(
            catalog=catalog,
            asset_id="source.sqlserver.unknown",
            expected_store=DataStoreType.SQLSERVER,
        )

    assert error_info.value.code == (SamplingAuthorizationCode.ASSET_NOT_FOUND)


@pytest.mark.parametrize(
    "asset_id",
    [
        "",
        " source.sqlserver.users",
        "source.sqlserver.users ",
    ],
)
def test_invalid_asset_id_is_rejected_before_catalog_lookup(
    asset_id: str,
) -> None:
    """Reject blank or whitespace-padded asset identifiers."""

    catalog = _catalog(
        _sqlserver_asset(),
    )

    with pytest.raises(ValueError):
        authorize_sampling_asset(
            catalog=catalog,
            asset_id=asset_id,
            expected_store=DataStoreType.SQLSERVER,
        )


def test_store_mismatch_is_rejected() -> None:
    """Prevent one sampling engine from using another store's asset."""

    catalog = _catalog(
        _sqlserver_asset(),
    )

    with pytest.raises(
        SamplingAuthorizationError,
        match="does not belong to the requested store",
    ) as error_info:
        authorize_sampling_asset(
            catalog=catalog,
            asset_id="source.sqlserver.users",
            expected_store=DataStoreType.MONGODB,
        )

    assert error_info.value.code == (SamplingAuthorizationCode.STORE_MISMATCH)


def test_disabled_sampling_is_rejected() -> None:
    """Reject a valid catalog asset whose sampling policy is disabled."""

    catalog = _catalog(
        _sqlserver_asset(
            sampling=_disabled_sampling(),
        ),
    )

    with pytest.raises(
        SamplingAuthorizationError,
        match="Sampling is disabled",
    ) as error_info:
        authorize_sampling_asset(
            catalog=catalog,
            asset_id="source.sqlserver.users",
            expected_store=DataStoreType.SQLSERVER,
        )

    assert error_info.value.code == (SamplingAuthorizationCode.SAMPLING_DISABLED)


def test_missing_read_permission_is_defensively_rejected() -> None:
    """Reject malformed catalog data that enables sampling without READ."""

    invalid_sampling = SamplingConfig.model_construct(
        enabled=True,
        max_rows=5,
        redact_fields=(),
    )

    invalid_asset = AssetCatalogEntry.model_construct(
        asset_id="platform.sqlserver.users",
        store=DataStoreType.SQLSERVER,
        database="return_platform",
        namespace="dbo",
        object_name="users",
        object_kind=ObjectKind.TABLE,
        ownership=OwnershipClass.PLATFORM_OWNED,
        authoritative=False,
        allowed_operations=(AllowedOperation.WRITE,),
        sampling=invalid_sampling,
    )

    catalog = _unsafe_catalog(
        invalid_asset,
    )

    with pytest.raises(
        SamplingAuthorizationError,
        match="does not allow reading",
    ) as error_info:
        authorize_sampling_asset(
            catalog=catalog,
            asset_id="platform.sqlserver.users",
            expected_store=DataStoreType.SQLSERVER,
        )

    assert error_info.value.code == (SamplingAuthorizationCode.READ_NOT_ALLOWED)


@pytest.mark.parametrize(
    "max_rows",
    [
        0,
        MAX_SAMPLE_ROWS + 1,
    ],
)
def test_invalid_row_limit_is_defensively_rejected(
    max_rows: int,
) -> None:
    """Reject malformed catalog policies outside the safe row bound."""

    invalid_sampling = SamplingConfig.model_construct(
        enabled=True,
        max_rows=max_rows,
        redact_fields=(),
    )

    invalid_asset = AssetCatalogEntry.model_construct(
        asset_id="source.sqlserver.users",
        store=DataStoreType.SQLSERVER,
        database="return_platform",
        namespace="dbo",
        object_name="users",
        object_kind=ObjectKind.TABLE,
        ownership=OwnershipClass.SOURCE_SYSTEM,
        authoritative=True,
        allowed_operations=(AllowedOperation.READ,),
        sampling=invalid_sampling,
    )

    catalog = _unsafe_catalog(
        invalid_asset,
    )

    with pytest.raises(
        SamplingAuthorizationError,
        match="outside the safe bound",
    ) as error_info:
        authorize_sampling_asset(
            catalog=catalog,
            asset_id="source.sqlserver.users",
            expected_store=DataStoreType.SQLSERVER,
        )

    assert error_info.value.code == (SamplingAuthorizationCode.INVALID_ROW_LIMIT)


def test_case_insensitive_duplicate_redaction_fields_are_rejected() -> None:
    """Prevent ambiguous redaction caused by SQL column-name casing."""

    invalid_sampling = SamplingConfig.model_construct(
        enabled=True,
        max_rows=5,
        redact_fields=(
            "email",
            "Email",
        ),
    )

    invalid_asset = AssetCatalogEntry.model_construct(
        asset_id="source.sqlserver.users",
        store=DataStoreType.SQLSERVER,
        database="return_platform",
        namespace="dbo",
        object_name="users",
        object_kind=ObjectKind.TABLE,
        ownership=OwnershipClass.SOURCE_SYSTEM,
        authoritative=True,
        allowed_operations=(AllowedOperation.READ,),
        sampling=invalid_sampling,
    )

    catalog = _unsafe_catalog(
        invalid_asset,
    )

    with pytest.raises(
        SamplingAuthorizationError,
        match="case-insensitive duplicates",
    ) as error_info:
        authorize_sampling_asset(
            catalog=catalog,
            asset_id="source.sqlserver.users",
            expected_store=DataStoreType.SQLSERVER,
        )

    assert error_info.value.code == (SamplingAuthorizationCode.INVALID_REDACTION_CONFIGURATION)


def test_sqlserver_collection_kind_is_defensively_rejected() -> None:
    """Reject a malformed SQL Server asset using MongoDB object kind."""

    invalid_asset = AssetCatalogEntry.model_construct(
        asset_id="source.sqlserver.users",
        store=DataStoreType.SQLSERVER,
        database="return_platform",
        namespace="dbo",
        object_name="users",
        object_kind=ObjectKind.COLLECTION,
        ownership=OwnershipClass.SOURCE_SYSTEM,
        authoritative=True,
        allowed_operations=(AllowedOperation.READ,),
        sampling=_enabled_sampling(),
    )

    catalog = _unsafe_catalog(
        invalid_asset,
    )

    with pytest.raises(
        SamplingAuthorizationError,
        match="tables and views",
    ) as error_info:
        authorize_sampling_asset(
            catalog=catalog,
            asset_id="source.sqlserver.users",
            expected_store=DataStoreType.SQLSERVER,
        )

    assert error_info.value.code == (SamplingAuthorizationCode.UNSUPPORTED_OBJECT_KIND)


def test_sqlserver_missing_namespace_is_defensively_rejected() -> None:
    """Reject a malformed SQL Server asset without a schema."""

    invalid_asset = AssetCatalogEntry.model_construct(
        asset_id="source.sqlserver.users",
        store=DataStoreType.SQLSERVER,
        database="return_platform",
        namespace=None,
        object_name="users",
        object_kind=ObjectKind.TABLE,
        ownership=OwnershipClass.SOURCE_SYSTEM,
        authoritative=True,
        allowed_operations=(AllowedOperation.READ,),
        sampling=_enabled_sampling(),
    )

    catalog = _unsafe_catalog(
        invalid_asset,
    )

    with pytest.raises(
        SamplingAuthorizationError,
        match="requires a cataloged namespace",
    ) as error_info:
        authorize_sampling_asset(
            catalog=catalog,
            asset_id="source.sqlserver.users",
            expected_store=DataStoreType.SQLSERVER,
        )

    assert error_info.value.code == (SamplingAuthorizationCode.UNSUPPORTED_OBJECT_KIND)


def test_mongodb_namespace_is_defensively_rejected() -> None:
    """Reject a malformed MongoDB asset with a SQL namespace."""

    invalid_asset = AssetCatalogEntry.model_construct(
        asset_id="source.mongodb.sessions",
        store=DataStoreType.MONGODB,
        database="return_platform",
        namespace="dbo",
        object_name="sessions",
        object_kind=ObjectKind.COLLECTION,
        ownership=OwnershipClass.SOURCE_SYSTEM,
        authoritative=True,
        allowed_operations=(AllowedOperation.READ,),
        sampling=_enabled_sampling(),
    )

    catalog = _unsafe_catalog(
        invalid_asset,
    )

    with pytest.raises(
        SamplingAuthorizationError,
        match="must not use a namespace",
    ) as error_info:
        authorize_sampling_asset(
            catalog=catalog,
            asset_id="source.mongodb.sessions",
            expected_store=DataStoreType.MONGODB,
        )

    assert error_info.value.code == (SamplingAuthorizationCode.UNSUPPORTED_OBJECT_KIND)


def test_ambiguous_catalog_asset_id_is_rejected() -> None:
    """Reject malformed catalogs containing duplicate asset IDs."""

    catalog_asset = _sqlserver_asset()

    catalog = _unsafe_catalog(
        catalog_asset,
        catalog_asset,
    )

    with pytest.raises(
        SamplingAuthorizationError,
        match="identity is ambiguous",
    ) as error_info:
        authorize_sampling_asset(
            catalog=catalog,
            asset_id="source.sqlserver.users",
            expected_store=DataStoreType.SQLSERVER,
        )

    assert error_info.value.code == (SamplingAuthorizationCode.ASSET_NOT_FOUND)

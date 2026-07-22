"""Tests for declared-versus-observed data governance drift."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from return_platform.data_governance.drift import (
    AssetDriftRecord,
    DriftReport,
    DriftState,
    analyze_drift,
)
from return_platform.data_governance.inventory.contracts import (
    MongoCollectionMetadata,
    MongoDBInventory,
    SQLServerInventory,
    SQLServerSchemaMetadata,
    SQLServerTableMetadata,
    SQLServerViewMetadata,
)
from return_platform.shared.governance import (
    AllowedOperation,
    AssetCatalog,
    AssetCatalogEntry,
    DataStoreType,
    ObjectKind,
    OwnershipClass,
)

OBSERVED_AT = datetime(
    2026,
    7,
    20,
    5,
    30,
    tzinfo=UTC,
)


def _empty_catalog() -> AssetCatalog:
    """Create an empty immutable asset catalog."""

    return AssetCatalog(
        version="1.0",
        assets=(),
    )


def _empty_sqlserver_inventory() -> SQLServerInventory:
    """Create an empty SQL Server inventory snapshot."""

    return SQLServerInventory(
        database_name="return_platform",
        observed_at=OBSERVED_AT,
        schemas=(),
    )


def _empty_mongodb_inventory() -> MongoDBInventory:
    """Create an empty MongoDB inventory snapshot."""

    return MongoDBInventory(
        database_name="return_platform",
        observed_at=OBSERVED_AT,
        collections=(),
    )


def _source_sqlserver_asset(
    *,
    object_name: str = "sales",
    namespace: str = "dbo",
    object_kind: ObjectKind = ObjectKind.TABLE,
    asset_id: str | None = None,
) -> AssetCatalogEntry:
    """Create a valid read-only source-system SQL Server asset."""

    resolved_asset_id = (
        asset_id if asset_id is not None else f"source.sqlserver.{object_name.casefold()}"
    )

    return AssetCatalogEntry(
        asset_id=resolved_asset_id,
        store=DataStoreType.SQLSERVER,
        database="return_platform",
        namespace=namespace,
        object_name=object_name,
        object_kind=object_kind,
        ownership=OwnershipClass.SOURCE_SYSTEM,
        authoritative=True,
        allowed_operations=(AllowedOperation.READ,),
    )


def _source_mongodb_asset(
    *,
    object_name: str = "sessions",
    asset_id: str | None = None,
) -> AssetCatalogEntry:
    """Create a valid read-only source-system MongoDB asset."""

    resolved_asset_id = (
        asset_id if asset_id is not None else f"source.mongodb.{object_name.casefold()}"
    )

    return AssetCatalogEntry(
        asset_id=resolved_asset_id,
        store=DataStoreType.MONGODB,
        database="return_platform",
        namespace=None,
        object_name=object_name,
        object_kind=ObjectKind.COLLECTION,
        ownership=OwnershipClass.SOURCE_SYSTEM,
        authoritative=True,
        allowed_operations=(AllowedOperation.READ,),
    )


def _sqlserver_inventory_with_table(
    *,
    object_name: str = "sales",
    namespace: str = "dbo",
    schema_id: int = 1,
    object_id: int = 100,
) -> SQLServerInventory:
    """Create SQL Server inventory containing one table."""

    table = SQLServerTableMetadata(
        object_id=object_id,
        name=object_name,
        approximate_row_count=0,
        columns=(),
    )

    schema = SQLServerSchemaMetadata(
        schema_id=schema_id,
        name=namespace,
        tables=(table,),
        views=(),
    )

    return SQLServerInventory(
        database_name="return_platform",
        observed_at=OBSERVED_AT,
        schemas=(schema,),
    )


def _sqlserver_inventory_with_view(
    *,
    object_name: str = "reporting_view",
    namespace: str = "dbo",
    schema_id: int = 1,
    object_id: int = 200,
) -> SQLServerInventory:
    """Create SQL Server inventory containing one view."""

    view = SQLServerViewMetadata(
        object_id=object_id,
        name=object_name,
        columns=(),
    )

    schema = SQLServerSchemaMetadata(
        schema_id=schema_id,
        name=namespace,
        tables=(),
        views=(view,),
    )

    return SQLServerInventory(
        database_name="return_platform",
        observed_at=OBSERVED_AT,
        schemas=(schema,),
    )


def _mongodb_inventory_with_collection(
    *,
    object_name: str = "sessions",
) -> MongoDBInventory:
    """Create MongoDB inventory containing one collection."""

    collection = MongoCollectionMetadata(
        name=object_name,
        approximate_document_count=0,
        indexes=(),
    )

    return MongoDBInventory(
        database_name="return_platform",
        observed_at=OBSERVED_AT,
        collections=(collection,),
    )


def test_empty_catalog_and_complete_empty_inventories_is_drift_free() -> None:
    """Treat fully evaluated empty stores as a zero-drift state."""

    report = analyze_drift(
        _empty_catalog(),
        sqlserver_inventory=_empty_sqlserver_inventory(),
        mongodb_inventory=_empty_mongodb_inventory(),
    )

    assert report.catalog_version == "1.0"
    assert report.records == ()
    assert report.is_complete is True
    assert report.drift_count == 0
    assert report.not_evaluated_count == 0
    assert report.is_drift_free is True
    assert report.sqlserver_observed_at == OBSERVED_AT
    assert report.mongodb_observed_at == OBSERVED_AT
    assert report.analyzed_at.tzinfo is UTC


def test_empty_catalog_without_inventories_is_not_drift_free() -> None:
    """Never report drift-free when no physical evidence was evaluated."""

    report = analyze_drift(
        _empty_catalog(),
    )

    assert report.records == ()
    assert report.is_complete is False
    assert report.drift_count == 0
    assert report.not_evaluated_count == 0
    assert report.is_drift_free is False
    assert report.sqlserver_observed_at is None
    assert report.mongodb_observed_at is None


def test_declared_sqlserver_table_matches_observed_table() -> None:
    """Classify an exact SQL Server identity match."""

    catalog = AssetCatalog(
        version="1.0",
        assets=(_source_sqlserver_asset(),),
    )

    report = analyze_drift(
        catalog,
        sqlserver_inventory=_sqlserver_inventory_with_table(),
        mongodb_inventory=_empty_mongodb_inventory(),
    )

    assert report.is_complete is True
    assert report.is_drift_free is True
    assert report.drift_count == 0
    assert report.not_evaluated_count == 0
    assert len(report.records) == 1

    record = report.records[0]

    assert record.store == DataStoreType.SQLSERVER
    assert record.database == "return_platform"
    assert record.namespace == "dbo"
    assert record.object_name == "sales"
    assert record.object_kind == ObjectKind.TABLE
    assert record.drift_state == (DriftState.DECLARED_AND_OBSERVED)
    assert record.asset_id == "source.sqlserver.sales"
    assert record.ownership == OwnershipClass.SOURCE_SYSTEM


def test_declared_mongodb_collection_matches_observed_collection() -> None:
    """Classify an exact MongoDB collection identity match."""

    catalog = AssetCatalog(
        version="1.0",
        assets=(_source_mongodb_asset(),),
    )

    report = analyze_drift(
        catalog,
        sqlserver_inventory=_empty_sqlserver_inventory(),
        mongodb_inventory=_mongodb_inventory_with_collection(),
    )

    assert report.is_complete is True
    assert report.is_drift_free is True
    assert len(report.records) == 1

    record = report.records[0]

    assert record.store == DataStoreType.MONGODB
    assert record.database == "return_platform"
    assert record.namespace is None
    assert record.object_name == "sessions"
    assert record.object_kind == ObjectKind.COLLECTION
    assert record.drift_state == (DriftState.DECLARED_AND_OBSERVED)
    assert record.asset_id == "source.mongodb.sessions"
    assert record.ownership == OwnershipClass.SOURCE_SYSTEM


def test_declared_asset_is_missing_only_when_store_was_evaluated() -> None:
    """Classify a declared asset as missing after successful inventory."""

    catalog = AssetCatalog(
        version="1.0",
        assets=(_source_mongodb_asset(),),
    )

    report = analyze_drift(
        catalog,
        sqlserver_inventory=_empty_sqlserver_inventory(),
        mongodb_inventory=_empty_mongodb_inventory(),
    )

    assert report.is_complete is True
    assert report.is_drift_free is False
    assert report.drift_count == 1
    assert report.not_evaluated_count == 0
    assert len(report.records) == 1

    record = report.records[0]

    assert record.drift_state == (DriftState.DECLARED_BUT_MISSING)
    assert record.asset_id == "source.mongodb.sessions"
    assert record.ownership == OwnershipClass.SOURCE_SYSTEM


def test_declared_asset_is_not_evaluated_when_inventory_is_absent() -> None:
    """Do not misclassify unavailable inventory as physical absence."""

    catalog = AssetCatalog(
        version="1.0",
        assets=(_source_sqlserver_asset(),),
    )

    report = analyze_drift(
        catalog,
        mongodb_inventory=_empty_mongodb_inventory(),
    )

    assert report.is_complete is False
    assert report.is_drift_free is False
    assert report.drift_count == 0
    assert report.not_evaluated_count == 1
    assert len(report.records) == 1

    record = report.records[0]

    assert record.drift_state == (DriftState.DECLARED_NOT_EVALUATED)
    assert record.asset_id == "source.sqlserver.sales"
    assert report.sqlserver_observed_at is None
    assert report.mongodb_observed_at == OBSERVED_AT


def test_observed_sqlserver_view_without_catalog_entry_is_drift() -> None:
    """Classify an uncataloged SQL Server view."""

    report = analyze_drift(
        _empty_catalog(),
        sqlserver_inventory=_sqlserver_inventory_with_view(
            object_name="rogue_view",
        ),
        mongodb_inventory=_empty_mongodb_inventory(),
    )

    assert report.is_complete is True
    assert report.is_drift_free is False
    assert report.drift_count == 1
    assert len(report.records) == 1

    record = report.records[0]

    assert record.store == DataStoreType.SQLSERVER
    assert record.database == "return_platform"
    assert record.namespace == "dbo"
    assert record.object_name == "rogue_view"
    assert record.object_kind == ObjectKind.VIEW
    assert record.drift_state == (DriftState.OBSERVED_BUT_UNDECLARED)
    assert record.asset_id is None
    assert record.ownership is None


def test_observed_mongodb_collection_without_catalog_entry_is_drift() -> None:
    """Classify an uncataloged MongoDB collection."""

    report = analyze_drift(
        _empty_catalog(),
        sqlserver_inventory=_empty_sqlserver_inventory(),
        mongodb_inventory=_mongodb_inventory_with_collection(
            object_name="rogue_collection",
        ),
    )

    assert report.is_complete is True
    assert report.is_drift_free is False
    assert report.drift_count == 1
    assert len(report.records) == 1

    record = report.records[0]

    assert record.store == DataStoreType.MONGODB
    assert record.namespace is None
    assert record.object_name == "rogue_collection"
    assert record.object_kind == ObjectKind.COLLECTION
    assert record.drift_state == (DriftState.OBSERVED_BUT_UNDECLARED)


def test_object_kind_is_part_of_physical_identity() -> None:
    """Do not match a declared table to an observed view."""

    catalog = AssetCatalog(
        version="1.0",
        assets=(
            _source_sqlserver_asset(
                object_name="sales",
                object_kind=ObjectKind.TABLE,
            ),
        ),
    )

    report = analyze_drift(
        catalog,
        sqlserver_inventory=_sqlserver_inventory_with_view(
            object_name="sales",
        ),
        mongodb_inventory=_empty_mongodb_inventory(),
    )

    assert report.is_complete is True
    assert report.is_drift_free is False
    assert report.drift_count == 2
    assert len(report.records) == 2

    assert {record.drift_state for record in report.records} == {
        DriftState.DECLARED_BUT_MISSING,
        DriftState.OBSERVED_BUT_UNDECLARED,
    }


def test_namespace_is_part_of_sqlserver_physical_identity() -> None:
    """Do not match equal names from different SQL schemas."""

    catalog = AssetCatalog(
        version="1.0",
        assets=(
            _source_sqlserver_asset(
                object_name="sales",
                namespace="dbo",
            ),
        ),
    )

    report = analyze_drift(
        catalog,
        sqlserver_inventory=_sqlserver_inventory_with_table(
            object_name="sales",
            namespace="reporting",
        ),
        mongodb_inventory=_empty_mongodb_inventory(),
    )

    assert report.drift_count == 2

    assert {
        (
            record.namespace,
            record.drift_state,
        )
        for record in report.records
    } == {
        (
            "dbo",
            DriftState.DECLARED_BUT_MISSING,
        ),
        (
            "reporting",
            DriftState.OBSERVED_BUT_UNDECLARED,
        ),
    }


def test_database_name_is_part_of_physical_identity() -> None:
    """Do not match equal objects from different databases."""

    catalog = AssetCatalog(
        version="1.0",
        assets=(
            _source_mongodb_asset(
                object_name="sessions",
            ),
        ),
    )

    observed = MongoDBInventory(
        database_name="other_database",
        observed_at=OBSERVED_AT,
        collections=(
            MongoCollectionMetadata(
                name="sessions",
                approximate_document_count=0,
                indexes=(),
            ),
        ),
    )

    report = analyze_drift(
        catalog,
        sqlserver_inventory=_empty_sqlserver_inventory(),
        mongodb_inventory=observed,
    )

    assert report.drift_count == 2

    assert {
        (
            record.database,
            record.drift_state,
        )
        for record in report.records
    } == {
        (
            "return_platform",
            DriftState.DECLARED_BUT_MISSING,
        ),
        (
            "other_database",
            DriftState.OBSERVED_BUT_UNDECLARED,
        ),
    }


def test_matching_is_case_sensitive_and_does_not_infer_collation() -> None:
    """Preserve exact physical names instead of normalizing case."""

    catalog = AssetCatalog(
        version="1.0",
        assets=(
            _source_sqlserver_asset(
                object_name="Sales",
                asset_id="source.sqlserver.sales_uppercase",
            ),
        ),
    )

    report = analyze_drift(
        catalog,
        sqlserver_inventory=_sqlserver_inventory_with_table(
            object_name="sales",
        ),
        mongodb_inventory=_empty_mongodb_inventory(),
    )

    assert report.drift_count == 2

    assert {
        (
            record.object_name,
            record.drift_state,
        )
        for record in report.records
    } == {
        (
            "Sales",
            DriftState.DECLARED_BUT_MISSING,
        ),
        (
            "sales",
            DriftState.OBSERVED_BUT_UNDECLARED,
        ),
    }


def test_records_are_deterministically_sorted() -> None:
    """Order records by store and exact physical identity."""

    catalog = AssetCatalog(
        version="1.0",
        assets=(
            _source_sqlserver_asset(
                object_name="zeta_table",
            ),
            _source_mongodb_asset(
                object_name="alpha_collection",
            ),
        ),
    )

    report = analyze_drift(
        catalog,
        sqlserver_inventory=_empty_sqlserver_inventory(),
        mongodb_inventory=_empty_mongodb_inventory(),
    )

    assert [
        (
            record.store,
            record.object_name,
        )
        for record in report.records
    ] == [
        (
            DataStoreType.MONGODB,
            "alpha_collection",
        ),
        (
            DataStoreType.SQLSERVER,
            "zeta_table",
        ),
    ]


def test_partial_governance_metadata_is_rejected() -> None:
    """Reject records containing only part of the governance identity."""

    with pytest.raises(
        ValidationError,
        match=("Governance metadata must be either fully present or fully absent"),
    ):
        AssetDriftRecord(
            store=DataStoreType.MONGODB,
            database="return_platform",
            namespace=None,
            object_name="sessions",
            object_kind=ObjectKind.COLLECTION,
            drift_state=DriftState.DECLARED_BUT_MISSING,
            asset_id="source.mongodb.sessions",
            ownership=None,
        )


def test_undeclared_record_cannot_contain_governance_metadata() -> None:
    """Reject inferred ownership for an undeclared physical object."""

    with pytest.raises(
        ValidationError,
        match=("Undeclared assets must not have governance metadata"),
    ):
        AssetDriftRecord(
            store=DataStoreType.MONGODB,
            database="return_platform",
            namespace=None,
            object_name="rogue_collection",
            object_kind=ObjectKind.COLLECTION,
            drift_state=DriftState.OBSERVED_BUT_UNDECLARED,
            asset_id="source.mongodb.rogue_collection",
            ownership=OwnershipClass.SOURCE_SYSTEM,
        )


def test_declared_record_requires_governance_metadata() -> None:
    """Reject declared states that lose catalog governance data."""

    with pytest.raises(
        ValidationError,
        match="Declared assets must have governance metadata",
    ):
        AssetDriftRecord(
            store=DataStoreType.SQLSERVER,
            database="return_platform",
            namespace="dbo",
            object_name="sales",
            object_kind=ObjectKind.TABLE,
            drift_state=DriftState.DECLARED_NOT_EVALUATED,
            asset_id=None,
            ownership=None,
        )


def test_sqlserver_record_requires_namespace() -> None:
    """Reject malformed SQL Server physical identities."""

    with pytest.raises(
        ValidationError,
        match="SQL Server drift records require a namespace",
    ):
        AssetDriftRecord(
            store=DataStoreType.SQLSERVER,
            database="return_platform",
            namespace=None,
            object_name="sales",
            object_kind=ObjectKind.TABLE,
            drift_state=DriftState.OBSERVED_BUT_UNDECLARED,
        )


def test_mongodb_record_rejects_namespace() -> None:
    """Reject SQL-style namespaces on MongoDB identities."""

    with pytest.raises(
        ValidationError,
        match="MongoDB drift records must not use a namespace",
    ):
        AssetDriftRecord(
            store=DataStoreType.MONGODB,
            database="return_platform",
            namespace="dbo",
            object_name="sessions",
            object_kind=ObjectKind.COLLECTION,
            drift_state=DriftState.OBSERVED_BUT_UNDECLARED,
        )


def test_drift_report_rejects_duplicate_physical_identities() -> None:
    """Reject duplicate records for one physical object."""

    record = AssetDriftRecord(
        store=DataStoreType.MONGODB,
        database="return_platform",
        namespace=None,
        object_name="rogue_collection",
        object_kind=ObjectKind.COLLECTION,
        drift_state=DriftState.OBSERVED_BUT_UNDECLARED,
    )

    with pytest.raises(
        ValidationError,
        match="duplicate physical identities",
    ):
        DriftReport(
            catalog_version="1.0",
            analyzed_at=OBSERVED_AT,
            sqlserver_observed_at=OBSERVED_AT,
            mongodb_observed_at=OBSERVED_AT,
            records=(
                record,
                record,
            ),
        )


def test_drift_report_rejects_nondeterministic_record_order() -> None:
    """Reject records not ordered by exact physical identity."""

    sqlserver_record = AssetDriftRecord(
        store=DataStoreType.SQLSERVER,
        database="return_platform",
        namespace="dbo",
        object_name="sales",
        object_kind=ObjectKind.TABLE,
        drift_state=DriftState.OBSERVED_BUT_UNDECLARED,
    )

    mongodb_record = AssetDriftRecord(
        store=DataStoreType.MONGODB,
        database="return_platform",
        namespace=None,
        object_name="sessions",
        object_kind=ObjectKind.COLLECTION,
        drift_state=DriftState.OBSERVED_BUT_UNDECLARED,
    )

    with pytest.raises(
        ValidationError,
        match="deterministic ordering",
    ):
        DriftReport(
            catalog_version="1.0",
            analyzed_at=OBSERVED_AT,
            sqlserver_observed_at=OBSERVED_AT,
            mongodb_observed_at=OBSERVED_AT,
            records=(
                sqlserver_record,
                mongodb_record,
            ),
        )


def test_evaluated_store_rejects_not_evaluated_record() -> None:
    """Reject contradictory report evidence."""

    record = AssetDriftRecord(
        store=DataStoreType.SQLSERVER,
        database="return_platform",
        namespace="dbo",
        object_name="sales",
        object_kind=ObjectKind.TABLE,
        drift_state=DriftState.DECLARED_NOT_EVALUATED,
        asset_id="source.sqlserver.sales",
        ownership=OwnershipClass.SOURCE_SYSTEM,
    )

    with pytest.raises(
        ValidationError,
        match=("Evaluated stores cannot contain DECLARED_NOT_EVALUATED"),
    ):
        DriftReport(
            catalog_version="1.0",
            analyzed_at=OBSERVED_AT,
            sqlserver_observed_at=OBSERVED_AT,
            mongodb_observed_at=None,
            records=(record,),
        )


def test_unevaluated_store_rejects_confirmed_drift_record() -> None:
    """Reject physical conclusions without inventory evidence."""

    record = AssetDriftRecord(
        store=DataStoreType.SQLSERVER,
        database="return_platform",
        namespace="dbo",
        object_name="sales",
        object_kind=ObjectKind.TABLE,
        drift_state=DriftState.DECLARED_BUT_MISSING,
        asset_id="source.sqlserver.sales",
        ownership=OwnershipClass.SOURCE_SYSTEM,
    )

    with pytest.raises(
        ValidationError,
        match=("Unevaluated stores can contain only DECLARED_NOT_EVALUATED"),
    ):
        DriftReport(
            catalog_version="1.0",
            analyzed_at=OBSERVED_AT,
            sqlserver_observed_at=None,
            mongodb_observed_at=OBSERVED_AT,
            records=(record,),
        )

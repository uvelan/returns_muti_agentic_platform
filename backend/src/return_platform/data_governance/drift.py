"""Declared-versus-observed data governance drift analysis."""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    field_validator,
    model_validator,
)

from return_platform.data_governance.inventory.contracts import (
    MongoDBInventory,
    SQLServerInventory,
)
from return_platform.data_governance.inventory.contracts.base_contracts import (
    require_utc_timestamp,
)
from return_platform.shared.governance import (
    AssetCatalog,
    AssetCatalogEntry,
    DataStoreType,
    ObjectKind,
    OwnershipClass,
)

DriftIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=512,
    ),
]


class DriftState(StrEnum):
    """Governance state of one physical asset identity."""

    DECLARED_AND_OBSERVED = "DECLARED_AND_OBSERVED"
    DECLARED_BUT_MISSING = "DECLARED_BUT_MISSING"
    OBSERVED_BUT_UNDECLARED = "OBSERVED_BUT_UNDECLARED"
    DECLARED_NOT_EVALUATED = "DECLARED_NOT_EVALUATED"


@dataclass(frozen=True, slots=True)
class _PhysicalAssetKey:
    """Internal exact physical identity used for drift matching."""

    store: DataStoreType
    database: str
    namespace: str | None
    object_name: str
    object_kind: ObjectKind


class AssetDriftRecord(BaseModel):
    """Drift result for one exact physical asset identity."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )

    store: DataStoreType
    database: DriftIdentifier
    namespace: DriftIdentifier | None = None
    object_name: DriftIdentifier
    object_kind: ObjectKind
    drift_state: DriftState

    asset_id: DriftIdentifier | None = None
    ownership: OwnershipClass | None = None

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        """Require a store-compatible physical identity."""

        if self.store == DataStoreType.SQLSERVER:
            if self.namespace is None:
                raise ValueError(
                    "SQL Server drift records require a namespace.",
                )

            if self.object_kind not in {
                ObjectKind.TABLE,
                ObjectKind.VIEW,
            }:
                raise ValueError(
                    "SQL Server drift records must represent a table or view.",
                )

        if self.store == DataStoreType.MONGODB:
            if self.namespace is not None:
                raise ValueError(
                    "MongoDB drift records must not use a namespace.",
                )

            if self.object_kind != ObjectKind.COLLECTION:
                raise ValueError(
                    "MongoDB drift records must represent a collection.",
                )

        return self

    @model_validator(mode="after")
    def validate_governance_presence(self) -> Self:
        """Align declared governance metadata with the drift state."""

        declared_states = {
            DriftState.DECLARED_AND_OBSERVED,
            DriftState.DECLARED_BUT_MISSING,
            DriftState.DECLARED_NOT_EVALUATED,
        }
        is_declared = self.drift_state in declared_states

        has_asset_id = self.asset_id is not None
        has_ownership = self.ownership is not None

        if has_asset_id != has_ownership:
            raise ValueError(
                "Governance metadata must be either fully present or fully absent.",
            )

        has_governance = has_asset_id and has_ownership

        if is_declared and not has_governance:
            raise ValueError(
                "Declared assets must have governance metadata.",
            )

        if not is_declared and has_governance:
            raise ValueError(
                "Undeclared assets must not have governance metadata.",
            )

        return self


class DriftReport(BaseModel):
    """Immutable drift projection for one catalog and inventory snapshot."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )

    catalog_version: DriftIdentifier
    analyzed_at: datetime
    sqlserver_observed_at: datetime | None = None
    mongodb_observed_at: datetime | None = None
    records: tuple[AssetDriftRecord, ...] = ()

    @field_validator(
        "analyzed_at",
        "sqlserver_observed_at",
        "mongodb_observed_at",
    )
    @classmethod
    def validate_timestamps(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        """Require UTC for every available evidence timestamp."""

        if value is None:
            return None

        return require_utc_timestamp(value)

    @model_validator(mode="after")
    def validate_records(self) -> Self:
        """Require unique records in deterministic physical order."""

        keys = tuple(_record_key(record) for record in self.records)

        if len(set(keys)) != len(keys):
            raise ValueError(
                "Drift report contains duplicate physical identities.",
            )

        expected_order = tuple(
            sorted(
                keys,
                key=_physical_sort_key,
            ),
        )

        if keys != expected_order:
            raise ValueError(
                "Drift report records must use deterministic ordering.",
            )

        for record in self.records:
            store_was_evaluated = self._store_was_evaluated(
                record.store,
            )

            if record.drift_state == DriftState.DECLARED_NOT_EVALUATED and store_was_evaluated:
                raise ValueError(
                    "Evaluated stores cannot contain DECLARED_NOT_EVALUATED records.",
                )

            if record.drift_state != DriftState.DECLARED_NOT_EVALUATED and not store_was_evaluated:
                raise ValueError(
                    "Unevaluated stores can contain only DECLARED_NOT_EVALUATED records.",
                )

        return self

    def _store_was_evaluated(
        self,
        store: DataStoreType,
    ) -> bool:
        """Return whether the report contains evidence for a store."""

        if store == DataStoreType.SQLSERVER:
            return self.sqlserver_observed_at is not None

        if store == DataStoreType.MONGODB:
            return self.mongodb_observed_at is not None

        return False

    @property
    def is_complete(self) -> bool:
        """Return whether both configured stores were evaluated."""

        return self.sqlserver_observed_at is not None and self.mongodb_observed_at is not None

    @property
    def drift_count(self) -> int:
        """Return the number of confirmed drift records."""

        return sum(
            record.drift_state
            in {
                DriftState.DECLARED_BUT_MISSING,
                DriftState.OBSERVED_BUT_UNDECLARED,
            }
            for record in self.records
        )

    @property
    def not_evaluated_count(self) -> int:
        """Return the number of declared assets not evaluated."""

        return sum(
            record.drift_state == DriftState.DECLARED_NOT_EVALUATED for record in self.records
        )

    @property
    def is_drift_free(self) -> bool:
        """Return true only for complete evaluation with zero drift."""

        return self.is_complete and self.drift_count == 0 and self.not_evaluated_count == 0


def _catalog_key(
    asset: AssetCatalogEntry,
) -> _PhysicalAssetKey:
    """Construct an exact physical key from a catalog entry."""

    return _PhysicalAssetKey(
        store=asset.store,
        database=asset.database,
        namespace=asset.namespace,
        object_name=asset.object_name,
        object_kind=asset.object_kind,
    )


def _record_key(
    record: AssetDriftRecord,
) -> _PhysicalAssetKey:
    """Construct an exact physical key from a drift record."""

    return _PhysicalAssetKey(
        store=record.store,
        database=record.database,
        namespace=record.namespace,
        object_name=record.object_name,
        object_kind=record.object_kind,
    )


def _physical_sort_key(
    key: _PhysicalAssetKey,
) -> tuple[str, str, str, str, str]:
    """Return deterministic ordering without case normalization."""

    return (
        key.store.value,
        key.database,
        key.namespace or "",
        key.object_name,
        key.object_kind.value,
    )


def _build_catalog_index(
    catalog: AssetCatalog,
) -> dict[_PhysicalAssetKey, AssetCatalogEntry]:
    """Build a defensive physical-identity catalog index."""

    catalog_index: dict[
        _PhysicalAssetKey,
        AssetCatalogEntry,
    ] = {}

    for asset in catalog.assets:
        key = _catalog_key(asset)

        if key in catalog_index:
            raise ValueError(
                "Catalog contains a duplicate physical identity.",
            )

        catalog_index[key] = asset

    return catalog_index


def _collect_sqlserver_keys(
    inventory: SQLServerInventory,
) -> set[_PhysicalAssetKey]:
    """Collect exact SQL Server table and view identities."""

    observed: set[_PhysicalAssetKey] = set()

    for schema in inventory.schemas:
        for table in schema.tables:
            observed.add(
                _PhysicalAssetKey(
                    store=DataStoreType.SQLSERVER,
                    database=inventory.database_name,
                    namespace=schema.name,
                    object_name=table.name,
                    object_kind=ObjectKind.TABLE,
                ),
            )

        for view in schema.views:
            observed.add(
                _PhysicalAssetKey(
                    store=DataStoreType.SQLSERVER,
                    database=inventory.database_name,
                    namespace=schema.name,
                    object_name=view.name,
                    object_kind=ObjectKind.VIEW,
                ),
            )

    return observed


def _collect_mongodb_keys(
    inventory: MongoDBInventory,
) -> set[_PhysicalAssetKey]:
    """Collect exact MongoDB collection identities."""

    return {
        _PhysicalAssetKey(
            store=DataStoreType.MONGODB,
            database=inventory.database_name,
            namespace=None,
            object_name=collection.name,
            object_kind=ObjectKind.COLLECTION,
        )
        for collection in inventory.collections
    }


def _store_was_evaluated(
    *,
    store: DataStoreType,
    sqlserver_inventory: SQLServerInventory | None,
    mongodb_inventory: MongoDBInventory | None,
) -> bool:
    """Return whether physical evidence exists for a store."""

    if store == DataStoreType.SQLSERVER:
        return sqlserver_inventory is not None

    if store == DataStoreType.MONGODB:
        return mongodb_inventory is not None

    return False


def analyze_drift(
    catalog: AssetCatalog,
    *,
    sqlserver_inventory: SQLServerInventory | None = None,
    mongodb_inventory: MongoDBInventory | None = None,
) -> DriftReport:
    """Compare declared assets with exact observed physical identities."""

    catalog_index = _build_catalog_index(catalog)
    observed_keys: set[_PhysicalAssetKey] = set()

    if sqlserver_inventory is not None:
        observed_keys.update(
            _collect_sqlserver_keys(
                sqlserver_inventory,
            ),
        )

    if mongodb_inventory is not None:
        observed_keys.update(
            _collect_mongodb_keys(
                mongodb_inventory,
            ),
        )

    all_keys = set(catalog_index)
    all_keys.update(observed_keys)

    records: list[AssetDriftRecord] = []

    for key in sorted(
        all_keys,
        key=_physical_sort_key,
    ):
        catalog_asset = catalog_index.get(key)
        is_declared = catalog_asset is not None
        is_observed = key in observed_keys

        if is_declared and is_observed:
            drift_state = DriftState.DECLARED_AND_OBSERVED
        elif is_declared and _store_was_evaluated(
            store=key.store,
            sqlserver_inventory=sqlserver_inventory,
            mongodb_inventory=mongodb_inventory,
        ):
            drift_state = DriftState.DECLARED_BUT_MISSING
        elif is_declared:
            drift_state = DriftState.DECLARED_NOT_EVALUATED
        else:
            drift_state = DriftState.OBSERVED_BUT_UNDECLARED

        records.append(
            AssetDriftRecord(
                store=key.store,
                database=key.database,
                namespace=key.namespace,
                object_name=key.object_name,
                object_kind=key.object_kind,
                drift_state=drift_state,
                asset_id=(catalog_asset.asset_id if catalog_asset is not None else None),
                ownership=(catalog_asset.ownership if catalog_asset is not None else None),
            ),
        )

    return DriftReport(
        catalog_version=catalog.version,
        analyzed_at=datetime.now(UTC),
        sqlserver_observed_at=(
            sqlserver_inventory.observed_at if sqlserver_inventory is not None else None
        ),
        mongodb_observed_at=(
            mongodb_inventory.observed_at if mongodb_inventory is not None else None
        ),
        records=tuple(records),
    )

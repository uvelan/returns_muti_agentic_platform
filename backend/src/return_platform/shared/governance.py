"""Strict governance contracts for cataloged data assets."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

CatalogVersion = Literal["1.0"]

AssetId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=255,
        pattern=r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$",
    ),
]

CatalogName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
    ),
]

FieldPath = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
    ),
]


class OwnershipClass(StrEnum):
    """Governance ownership of a physical data asset."""

    SOURCE_SYSTEM = "SOURCE_SYSTEM"
    PLATFORM_OWNED = "PLATFORM_OWNED"
    DERIVED_PROJECTION = "DERIVED_PROJECTION"
    WORKFLOW_INTERNAL = "WORKFLOW_INTERNAL"


class DataStoreType(StrEnum):
    """Data stores supported by the Stage 3 inventory."""

    MONGODB = "MONGODB"
    SQLSERVER = "SQLSERVER"


class AllowedOperation(StrEnum):
    """Operations the Return Platform may perform on an asset."""

    READ = "READ"
    WRITE = "WRITE"
    DELETE = "DELETE"
    REBUILD = "REBUILD"


class ObjectKind(StrEnum):
    """Supported physical database object types."""

    TABLE = "TABLE"
    VIEW = "VIEW"
    COLLECTION = "COLLECTION"


class GovernanceModel(BaseModel):
    """Base model for immutable governance contracts."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


class SamplingConfig(GovernanceModel):
    """Permission and limits for future bounded sampling."""

    enabled: bool = False
    max_rows: int = Field(default=0, ge=0, le=25)
    redact_fields: tuple[FieldPath, ...] = ()

    @field_validator("redact_fields")
    @classmethod
    def reject_duplicate_redaction_fields(
        cls,
        value: tuple[FieldPath, ...],
    ) -> tuple[FieldPath, ...]:
        if len(value) != len(set(value)):
            raise ValueError(
                "redact_fields must not contain duplicates"
            )

        return value

    @model_validator(mode="after")
    def validate_sampling_configuration(self) -> Self:
        if self.enabled and self.max_rows == 0:
            raise ValueError(
                "max_rows must be between 1 and 25 "
                "when sampling is enabled"
            )

        if not self.enabled and self.max_rows != 0:
            raise ValueError(
                "max_rows must be 0 when sampling is disabled"
            )

        return self


class AssetCatalogEntry(GovernanceModel):
    """Declared governance boundary for one physical data asset."""

    asset_id: AssetId
    store: DataStoreType
    database: CatalogName
    namespace: CatalogName | None = None
    object_name: CatalogName
    object_kind: ObjectKind
    ownership: OwnershipClass
    authoritative: bool = False
    allowed_operations: tuple[AllowedOperation, ...] = Field(
        min_length=1
    )
    sampling: SamplingConfig = Field(
        default_factory=SamplingConfig
    )

    @field_validator(
        "database",
        "namespace",
        "object_name",
    )
    @classmethod
    def reject_control_characters(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        if any(ord(character) < 32 for character in value):
            raise ValueError(
                "catalog names must not contain control characters"
            )

        return value

    @field_validator("allowed_operations")
    @classmethod
    def reject_duplicate_operations(
        cls,
        value: tuple[AllowedOperation, ...],
    ) -> tuple[AllowedOperation, ...]:
        if len(value) != len(set(value)):
            raise ValueError(
                "allowed_operations must not contain duplicates"
            )

        return value

    @model_validator(mode="after")
    def validate_governance_boundary(self) -> Self:
        operations = set(self.allowed_operations)

        self._validate_asset_id_prefix()
        self._validate_store_object_compatibility()
        self._validate_ownership_permissions(operations)
        self._validate_sampling_permissions(operations)

        return self

    def physical_key(
        self,
    ) -> tuple[
        DataStoreType,
        str,
        str | None,
        str,
        ObjectKind,
    ]:
        """Return the stable identity of the physical asset."""

        return (
            self.store,
            self.database,
            self.namespace,
            self.object_name,
            self.object_kind,
        )

    def _validate_asset_id_prefix(self) -> None:
        expected_prefix = {
            OwnershipClass.SOURCE_SYSTEM: "source.",
            OwnershipClass.PLATFORM_OWNED: "platform.",
            OwnershipClass.DERIVED_PROJECTION: "derived.",
            OwnershipClass.WORKFLOW_INTERNAL: "workflow.",
        }[self.ownership]

        if not self.asset_id.startswith(expected_prefix):
            raise ValueError(
                f"asset_id for {self.ownership.value} must "
                f"start with {expected_prefix!r}"
            )

    def _validate_store_object_compatibility(self) -> None:
        if self.store is DataStoreType.SQLSERVER:
            if self.object_kind not in {
                ObjectKind.TABLE,
                ObjectKind.VIEW,
            }:
                raise ValueError(
                    "SQLSERVER assets must be TABLE or VIEW objects"
                )

            if self.namespace is None:
                raise ValueError(
                    "SQLSERVER assets must declare a schema namespace"
                )

            return

        if self.object_kind is not ObjectKind.COLLECTION:
            raise ValueError(
                "MONGODB assets must use object_kind COLLECTION"
            )

        if self.namespace is not None:
            raise ValueError(
                "MONGODB assets must not declare a schema namespace"
            )

    def _validate_ownership_permissions(
        self,
        operations: set[AllowedOperation],
    ) -> None:
        if (
            self.ownership is OwnershipClass.SOURCE_SYSTEM
            and operations != {AllowedOperation.READ}
        ):
            raise ValueError(
                "SOURCE_SYSTEM assets are strictly read-only"
            )

        if (
            self.object_kind is ObjectKind.VIEW
            and operations != {AllowedOperation.READ}
        ):
            raise ValueError(
                "VIEW assets are strictly read-only"
            )

        if (
            self.ownership
            is OwnershipClass.DERIVED_PROJECTION
            and self.authoritative
        ):
            raise ValueError(
                "DERIVED_PROJECTION assets cannot be authoritative"
            )

    def _validate_sampling_permissions(
        self,
        operations: set[AllowedOperation],
    ) -> None:
        if (
            self.sampling.enabled
            and AllowedOperation.READ not in operations
        ):
            raise ValueError(
                "sampling requires the READ operation"
            )


class AssetCatalog(GovernanceModel):
    """Root contract for the version-controlled asset catalog."""

    version: CatalogVersion = "1.0"
    assets: tuple[AssetCatalogEntry, ...] = ()

    @model_validator(mode="after")
    def validate_catalog_uniqueness(self) -> Self:
        asset_ids: set[str] = set()
        physical_assets: set[
            tuple[
                DataStoreType,
                str,
                str | None,
                str,
                ObjectKind,
            ]
        ] = set()

        for asset in self.assets:
            if asset.asset_id in asset_ids:
                raise ValueError(
                    f"duplicate asset_id: {asset.asset_id}"
                )

            asset_ids.add(asset.asset_id)

            physical_key = asset.physical_key()

            if physical_key in physical_assets:
                raise ValueError(
                    "multiple catalog entries reference the same "
                    "physical asset: "
                    f"{asset.store.value}/"
                    f"{asset.database}/"
                    f"{asset.namespace or '-'}/"
                    f"{asset.object_name}/"
                    f"{asset.object_kind.value}"
                )

            physical_assets.add(physical_key)

        return self

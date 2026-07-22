"""Strict contracts for physically observed SQL Server metadata."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from return_platform.data_governance.inventory.contracts.base_contracts import (
    ObservedMetadataModel,
    require_strictly_ascending_integers,
    require_unique,
    require_utc_timestamp,
)

SQLIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
    ),
]


class SQLServerRowCountSource(StrEnum):
    """Metadata source used for approximate SQL Server row counts."""

    SYS_PARTITIONS = "SYS_PARTITIONS"


class SQLServerDataTypeMetadata(ObservedMetadataModel):
    """Observed SQL Server type identity and storage attributes."""

    schema_name: SQLIdentifier
    name: SQLIdentifier
    is_user_defined: bool
    max_length_bytes: int = Field(ge=-1)
    precision: int = Field(ge=0, le=38)
    scale: int = Field(ge=0, le=38)

    @model_validator(mode="after")
    def validate_precision_and_scale(self) -> Self:
        """Reject impossible precision and scale combinations."""

        if self.scale > self.precision:
            raise ValueError(
                "Data type scale cannot exceed precision.",
            )

        return self


class SQLServerColumnMetadata(ObservedMetadataModel):
    """Observed metadata for one SQL Server column."""

    column_id: int = Field(ge=1)
    name: SQLIdentifier
    data_type: SQLServerDataTypeMetadata
    is_nullable: bool
    is_identity: bool
    is_computed: bool
    collation_name: SQLIdentifier | None = None


class SQLServerTableMetadata(ObservedMetadataModel):
    """Observed physical metadata for one SQL Server user table."""

    object_id: int = Field(ge=1)
    name: SQLIdentifier
    approximate_row_count: int = Field(ge=0)
    row_count_source: SQLServerRowCountSource = SQLServerRowCountSource.SYS_PARTITIONS
    columns: tuple[SQLServerColumnMetadata, ...] = ()

    @model_validator(mode="after")
    def validate_columns(self) -> Self:
        """Require unique, physically ordered table columns."""

        require_unique(
            (column.column_id for column in self.columns),
            label="table column ID",
        )
        require_unique(
            (column.name for column in self.columns),
            label="table column name",
        )
        require_strictly_ascending_integers(
            tuple(column.column_id for column in self.columns),
            label="Table column IDs",
        )

        return self


class SQLServerViewMetadata(ObservedMetadataModel):
    """Observed physical metadata for one SQL Server user view."""

    object_id: int = Field(ge=1)
    name: SQLIdentifier
    columns: tuple[SQLServerColumnMetadata, ...] = ()

    @model_validator(mode="after")
    def validate_columns(self) -> Self:
        """Require unique, physically ordered view columns."""

        require_unique(
            (column.column_id for column in self.columns),
            label="view column ID",
        )
        require_unique(
            (column.name for column in self.columns),
            label="view column name",
        )
        require_strictly_ascending_integers(
            tuple(column.column_id for column in self.columns),
            label="View column IDs",
        )

        return self


class SQLServerSchemaMetadata(ObservedMetadataModel):
    """Observed SQL Server schema containing user tables and views."""

    schema_id: int = Field(ge=1)
    name: SQLIdentifier
    tables: tuple[SQLServerTableMetadata, ...] = ()
    views: tuple[SQLServerViewMetadata, ...] = ()

    @model_validator(mode="after")
    def validate_objects(self) -> Self:
        """Reject duplicate or nondeterministically ordered objects."""

        table_object_ids = tuple(table.object_id for table in self.tables)
        view_object_ids = tuple(view.object_id for view in self.views)
        table_names = tuple(table.name for table in self.tables)
        view_names = tuple(view.name for view in self.views)

        require_unique(
            table_object_ids,
            label="table object ID",
        )
        require_unique(
            table_names,
            label="table name",
        )
        require_unique(
            view_object_ids,
            label="view object ID",
        )
        require_unique(
            view_names,
            label="view name",
        )
        require_unique(
            (
                *table_object_ids,
                *view_object_ids,
            ),
            label="schema object ID",
        )
        require_unique(
            (
                *table_names,
                *view_names,
            ),
            label="schema object name",
        )
        require_strictly_ascending_integers(
            table_object_ids,
            label="Table object IDs",
        )
        require_strictly_ascending_integers(
            view_object_ids,
            label="View object IDs",
        )

        return self


class SQLServerInventory(ObservedMetadataModel):
    """Caller-visible metadata observed in one SQL Server database."""

    database_name: SQLIdentifier
    observed_at: datetime
    schemas: tuple[SQLServerSchemaMetadata, ...] = ()

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(
        cls,
        value: datetime,
    ) -> datetime:
        """Require a timezone-aware UTC observation timestamp."""

        return require_utc_timestamp(value)

    @model_validator(mode="after")
    def validate_schemas(self) -> Self:
        """Require unique schemas in deterministic ID order."""

        schema_ids = tuple(schema.schema_id for schema in self.schemas)
        schema_names = tuple(schema.name for schema in self.schemas)

        require_unique(
            schema_ids,
            label="schema ID",
        )
        require_unique(
            schema_names,
            label="schema name",
        )
        require_strictly_ascending_integers(
            schema_ids,
            label="Schema IDs",
        )

        table_object_ids = tuple(
            table.object_id for schema in self.schemas for table in schema.tables
        )
        view_object_ids = tuple(view.object_id for schema in self.schemas for view in schema.views)

        require_unique(
            (
                *table_object_ids,
                *view_object_ids,
            ),
            label="database object ID",
        )

        return self

    @property
    def table_count(self) -> int:
        """Return the number of observed user tables."""

        return sum(len(schema.tables) for schema in self.schemas)

    @property
    def view_count(self) -> int:
        """Return the number of observed user views."""

        return sum(len(schema.views) for schema in self.schemas)

    @property
    def is_empty(self) -> bool:
        """Return whether no user tables or views were observed."""

        return self.table_count == 0 and self.view_count == 0

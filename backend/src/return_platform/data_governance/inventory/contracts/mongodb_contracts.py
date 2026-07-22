"""Strict contracts for physically observed MongoDB metadata."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from return_platform.data_governance.inventory.contracts.base_contracts import (
    ObservedMetadataModel,
    require_strictly_ascending_text,
    require_unique,
    require_utc_timestamp,
)

MongoIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
    ),
]

MongoIndexDirection = Literal[
    -1,
    1,
    "2d",
    "2dsphere",
    "hashed",
    "text",
]


class MongoDocumentCountSource(StrEnum):
    """Metadata operation used for an approximate document count."""

    ESTIMATED_DOCUMENT_COUNT = "ESTIMATED_DOCUMENT_COUNT"


class MongoIndexKeyMetadata(ObservedMetadataModel):
    """Observed field and direction within a MongoDB index."""

    field_name: MongoIdentifier
    direction: MongoIndexDirection

    @field_validator(
        "direction",
        mode="before",
    )
    @classmethod
    def reject_boolean_direction(
        cls,
        value: object,
    ) -> object:
        """Reject Boolean values masquerading as integers."""

        if isinstance(value, bool):
            raise ValueError(
                "MongoDB index direction must not be Boolean.",
            )

        return value


class MongoIndexMetadata(ObservedMetadataModel):
    """Observed physical metadata for one MongoDB index."""

    name: MongoIdentifier
    is_unique: bool
    is_sparse: bool
    is_hidden: bool
    expire_after_seconds: int | None = Field(
        default=None,
        ge=0,
    )
    has_partial_filter: bool
    keys: tuple[MongoIndexKeyMetadata, ...] = Field(
        min_length=1,
    )

    @model_validator(mode="after")
    def validate_keys(self) -> Self:
        """Require unique fields while preserving compound-index order."""

        require_unique(
            (key.field_name for key in self.keys),
            label="MongoDB index key field",
        )

        return self


class MongoCollectionMetadata(ObservedMetadataModel):
    """Observed physical metadata for one MongoDB collection."""

    name: MongoIdentifier
    approximate_document_count: int = Field(ge=0)
    document_count_source: MongoDocumentCountSource = (
        MongoDocumentCountSource.ESTIMATED_DOCUMENT_COUNT
    )
    indexes: tuple[MongoIndexMetadata, ...] = ()

    @model_validator(mode="after")
    def validate_indexes(self) -> Self:
        """Require unique indexes in deterministic name order."""

        index_names = tuple(index.name for index in self.indexes)

        require_unique(
            index_names,
            label="MongoDB collection index name",
        )
        require_strictly_ascending_text(
            index_names,
            label="MongoDB collection index names",
        )

        return self


class MongoDBInventory(ObservedMetadataModel):
    """Visible collections in the configured MongoDB database."""

    database_name: MongoIdentifier
    observed_at: datetime
    collections: tuple[MongoCollectionMetadata, ...] = ()

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(
        cls,
        value: datetime,
    ) -> datetime:
        """Require a timezone-aware UTC observation timestamp."""

        return require_utc_timestamp(value)

    @model_validator(mode="after")
    def validate_collections(self) -> Self:
        """Require unique collections in deterministic name order."""

        collection_names = tuple(collection.name for collection in self.collections)

        require_unique(
            collection_names,
            label="MongoDB collection name",
        )
        require_strictly_ascending_text(
            collection_names,
            label="MongoDB collection names",
        )

        return self

    @property
    def collection_count(self) -> int:
        """Return the number of visible user collections."""

        return len(self.collections)

    @property
    def index_count(self) -> int:
        """Return the number of visible collection indexes."""

        return sum(len(collection.indexes) for collection in self.collections)

    @property
    def is_empty(self) -> bool:
        """Return whether no user collections were observed."""

        return not self.collections

"""Version-controlled physical and graph schema registry for Data Console and seed tooling."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RegistryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SchemaField(RegistryModel):
    name: str = Field(min_length=1, max_length=255)
    type: Literal["string", "integer", "number", "boolean", "datetime", "object", "array"]
    required: bool = False
    key: bool = False
    description: str = Field(min_length=1, max_length=500)
    generator: str | None = Field(default=None, min_length=1, max_length=128)
    items: str | None = Field(default=None, min_length=1, max_length=128)


class DataAssetSchema(RegistryModel):
    asset_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$", max_length=255)
    engine: Literal["MONGODB", "SQLSERVER"]
    database: str = Field(min_length=1, max_length=128)
    namespace: str | None = Field(default=None, min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    ownership: Literal["SOURCE_SYSTEM", "PLATFORM_OWNED", "DERIVED_PROJECTION"]
    authoritative: bool
    writable_in_sandbox: bool
    description: str = Field(min_length=1, max_length=500)
    fields: tuple[SchemaField, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_asset(self) -> Self:
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise ValueError(f"Duplicate field in {self.asset_id}")
        if self.engine == "SQLSERVER" and self.namespace is None:
            raise ValueError(f"SQL Server asset {self.asset_id} requires namespace")
        if self.engine == "MONGODB" and self.namespace is not None:
            raise ValueError(f"MongoDB asset {self.asset_id} cannot declare namespace")
        if not any(field.key for field in self.fields):
            raise ValueError(f"Asset {self.asset_id} must declare at least one key field")
        return self


class GraphNodeSchema(RegistryModel):
    label: str = Field(pattern=r"^[A-Z][A-Za-z0-9_]{0,63}$")
    key_property: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    source_assets: tuple[str, ...] = Field(min_length=1)
    properties: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_properties(self) -> Self:
        if self.key_property not in self.properties:
            raise ValueError(f"Graph node {self.label} key property must be projected")
        if len(self.properties) != len(set(self.properties)):
            raise ValueError(f"Graph node {self.label} has duplicate properties")
        return self


class GraphRelationshipSchema(RegistryModel):
    type: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    from_label: str = Field(pattern=r"^[A-Z][A-Za-z0-9_]{0,63}$")
    to_label: str = Field(pattern=r"^[A-Z][A-Za-z0-9_]{0,63}$")
    from_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    to_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")


class GraphSchema(RegistryModel):
    nodes: tuple[GraphNodeSchema, ...] = Field(min_length=1)
    relationships: tuple[GraphRelationshipSchema, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        labels = [node.label for node in self.nodes]
        if len(labels) != len(set(labels)):
            raise ValueError("Graph node labels must be unique")
        label_set = set(labels)
        relationship_types = [relationship.type for relationship in self.relationships]
        if len(relationship_types) != len(set(relationship_types)):
            raise ValueError("Graph relationship types must be unique")
        for relationship in self.relationships:
            if relationship.from_label not in label_set or relationship.to_label not in label_set:
                raise ValueError(f"Unknown graph label in {relationship.type}")
        return self


class SchemaRegistry(RegistryModel):
    schema_version: Literal["1.0"]
    assets: tuple[DataAssetSchema, ...] = Field(min_length=1)
    graph: GraphSchema

    @model_validator(mode="after")
    def validate_registry(self) -> Self:
        asset_ids = [asset.asset_id for asset in self.assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("Schema registry asset IDs must be unique")
        physical = [
            (asset.engine, asset.database, asset.namespace, asset.name) for asset in self.assets
        ]
        if len(physical) != len(set(physical)):
            raise ValueError("Schema registry physical assets must be unique")
        known = set(asset_ids)
        for node in self.graph.nodes:
            missing = set(node.source_assets) - known
            if missing:
                raise ValueError(f"Graph node {node.label} references unknown assets: {missing}")
        return self

    def asset(self, asset_id: str) -> DataAssetSchema:
        for asset in self.assets:
            if asset.asset_id == asset_id:
                return asset
        raise KeyError(asset_id)


@lru_cache(maxsize=8)
def load_schema_registry(path: str | Path) -> SchemaRegistry:
    resolved = Path(path).expanduser().resolve(strict=True)
    if resolved.suffix.lower() not in {".yaml", ".yml"}:
        raise ValueError("Schema registry must be YAML")
    if resolved.stat().st_size > 1_048_576:
        raise ValueError("Schema registry exceeds 1 MiB")
    document = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    return SchemaRegistry.model_validate(document)

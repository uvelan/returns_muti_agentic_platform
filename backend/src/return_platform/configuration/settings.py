from pathlib import Path
from typing import Literal, Self

from pydantic import (
    AnyHttpUrl,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_DATA_ASSET_CATALOG_PATH = BACKEND_ROOT / "config" / "data_assets.yaml"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PLATFORM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        case_sensitive=False,
        validate_default=True,
        frozen=True,
    )

    catalog_path: Path = Field(
        default=DEFAULT_DATA_ASSET_CATALOG_PATH,
        description=("Absolute path to the version-controlled data asset catalog."),
    )

    environment: Literal[
        "development",
        "test",
        "staging",
        "production",
    ] = "development"

    probe_timeout_seconds: float = Field(
        default=2.0,
        gt=0.0,
        le=30.0,
    )
    dependency_connect_timeout_seconds: float = Field(
        default=5.0,
        gt=0.0,
        le=30.0,
    )

    frontend_cors_origin: AnyHttpUrl

    mongo_dsn: SecretStr = Field(min_length=10)

    neo4j_uri: str = Field(min_length=10)
    neo4j_user: str = Field(
        default="neo4j",
        min_length=1,
    )
    neo4j_password: SecretStr = Field(min_length=1)

    valkey_host: str = Field(min_length=1)
    valkey_port: int = Field(
        default=6379,
        ge=1,
        le=65_535,
    )
    valkey_password: SecretStr = Field(min_length=1)

    temporal_target: str = Field(min_length=3)

    sqlserver_host: str = Field(min_length=1)
    sqlserver_port: int = Field(
        default=1433,
        ge=1,
        le=65_535,
    )
    sqlserver_user: str = Field(
        default="sa",
        min_length=1,
    )
    sqlserver_password: SecretStr = Field(min_length=1)
    sqlserver_database: str = Field(min_length=1)
    mongo_database: str = Field(
        default="return_platform",
        min_length=1,
        max_length=63,
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,62}$",
    )
    graph_evidence_collection: str = Field(
        default="graph_evidence_runs",
        min_length=1,
        max_length=127,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,126}$",
    )
    graph_evidence_query_timeout_seconds: float = Field(
        default=5.0,
        ge=0.05,
        le=30.0,
    )

    @field_validator("catalog_path")
    @classmethod
    def validate_catalog_path(
        cls,
        value: Path,
    ) -> Path:
        resolved_path = value.expanduser()

        if not resolved_path.is_absolute():
            raise ValueError(
                "catalog_path must be an absolute path",
            )

        if resolved_path.suffix.lower() not in {
            ".yaml",
            ".yml",
        }:
            raise ValueError(
                "catalog_path must reference a .yaml or .yml file",
            )

        return resolved_path.resolve(strict=False)

    @field_validator(
        "neo4j_user",
        "valkey_host",
        "temporal_target",
        "sqlserver_host",
        "sqlserver_user",
        "sqlserver_database",
    )
    @classmethod
    def reject_blank_strings(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError("Value must not be blank.")

        return normalized

    @field_validator(
        "mongo_dsn",
        "neo4j_password",
        "valkey_password",
        "sqlserver_password",
    )
    @classmethod
    def reject_blank_secrets(
        cls,
        value: SecretStr,
    ) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Secret must not be blank.")

        return value

    @field_validator("mongo_dsn")
    @classmethod
    def validate_mongo_dsn(
        cls,
        value: SecretStr,
    ) -> SecretStr:
        raw_value = value.get_secret_value()

        if not raw_value.startswith(
            (
                "mongodb://",
                "mongodb+srv://",
            ),
        ):
            raise ValueError(
                "MongoDB DSN must start with mongodb:// or mongodb+srv://.",
            )

        return value

    @field_validator("neo4j_uri")
    @classmethod
    def validate_neo4j_uri(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        allowed_schemes = (
            "bolt://",
            "bolt+s://",
            "bolt+ssc://",
            "neo4j://",
            "neo4j+s://",
            "neo4j+ssc://",
        )

        if not normalized.startswith(allowed_schemes):
            raise ValueError(
                "Neo4j URI uses an unsupported scheme.",
            )

        return normalized

    @field_validator("frontend_cors_origin")
    @classmethod
    def validate_cors_origin(
        cls,
        value: AnyHttpUrl,
    ) -> AnyHttpUrl:
        if value.path not in ("", "/") or value.query or value.fragment:
            raise ValueError(
                "CORS origin must contain only scheme, host, and optional port.",
            )

        return value

    @field_validator("temporal_target")
    @classmethod
    def validate_temporal_target(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if ":" not in normalized:
            raise ValueError(
                "Temporal target must include host and port.",
            )

        host, port_text = normalized.rsplit(
            ":",
            maxsplit=1,
        )

        if not host:
            raise ValueError(
                "Temporal host must not be empty.",
            )

        try:
            port = int(port_text)
        except ValueError as error:
            raise ValueError(
                "Temporal port must be an integer.",
            ) from error

        if not 1 <= port <= 65_535:
            raise ValueError(
                "Temporal port must be between 1 and 65535.",
            )

        return normalized

    @model_validator(mode="after")
    def validate_timeout_relationship(self) -> Self:
        if self.dependency_connect_timeout_seconds < self.probe_timeout_seconds:
            raise ValueError(
                "Connection timeout must be greater than or equal to probe timeout.",
            )

        return self

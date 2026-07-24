from pathlib import Path
from typing import Literal, Self

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_ASSET_CATALOG_PATH = BACKEND_ROOT / "config" / "data_assets.yaml"


class Settings(BaseSettings):
    """Validated process configuration. Secrets are never serialized by application APIs."""

    model_config = SettingsConfigDict(
        env_prefix="PLATFORM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        validate_default=True,
        frozen=True,
    )

    catalog_path: Path = Field(default=DEFAULT_DATA_ASSET_CATALOG_PATH)
    environment: Literal["development", "test", "staging", "production"] = "development"

    probe_timeout_seconds: float = Field(default=2.0, gt=0.0, le=30.0)
    dependency_connect_timeout_seconds: float = Field(default=5.0, gt=0.0, le=30.0)
    operation_timeout_seconds: float = Field(default=10.0, ge=0.1, le=120.0)

    frontend_cors_origin: AnyHttpUrl

    mongo_dsn: SecretStr = Field(min_length=10)
    mongo_database: str = Field(
        default="return_platform",
        min_length=1,
        max_length=63,
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,62}$",
    )
    source_mongo_dsn: SecretStr | None = None
    source_mongo_database: str = Field(default="return_source", min_length=1, max_length=63)

    neo4j_uri: str = Field(min_length=10)
    neo4j_user: str = Field(default="neo4j", min_length=1)
    neo4j_password: SecretStr = Field(min_length=1)
    neo4j_database: str = Field(default="neo4j", min_length=1)

    valkey_host: str = Field(min_length=1)
    valkey_port: int = Field(default=6379, ge=1, le=65_535)
    valkey_password: SecretStr = Field(min_length=1)
    event_stream_retention: int = Field(default=10_000, ge=1_000, le=1_000_000)
    sse_heartbeat_seconds: float = Field(default=15.0, ge=5.0, le=60.0)
    sse_replay_limit: int = Field(default=1_000, ge=1, le=10_000)

    temporal_target: str = Field(min_length=3)
    return_workflow_task_queue: str = Field(
        default="return-platform-return-v1",
        pattern=r"^[a-z][a-z0-9-]{0,126}$",
    )
    orchestration_poll_seconds: float = Field(default=1.0, ge=0.1, le=30.0)
    worker_readiness_ttl_seconds: int = Field(default=30, ge=5, le=300)

    sqlserver_host: str = Field(min_length=1)
    sqlserver_port: int = Field(default=1433, ge=1, le=65_535)
    sqlserver_user: str = Field(default="sa", min_length=1)
    sqlserver_password: SecretStr = Field(min_length=1)
    sqlserver_database: str = Field(min_length=1)

    graph_evidence_collection: str = Field(
        default="graph_evidence_runs",
        min_length=1,
        max_length=127,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,126}$",
    )
    graph_evidence_query_timeout_seconds: float = Field(default=5.0, ge=0.05, le=30.0)

    ai_provider_order: str = "GOOGLE,NVIDIA,OPENAI,ANTHROPIC,OLLAMA,SIMULATOR"
    ai_timeout_seconds: float = Field(default=12.0, ge=0.5, le=60.0)
    ai_global_timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)
    ai_max_attempts_per_provider: int = Field(default=2, ge=1, le=4)
    ai_max_concurrency: int = Field(default=16, ge=1, le=256)
    ai_requests_per_minute: int = Field(default=120, ge=1, le=100_000)
    ai_max_payload_bytes: int = Field(default=16_384, ge=1_024, le=1_048_576)
    ai_interception_default: bool = False
    ai_prompt_version: str = Field(default="return-eligibility-v1", min_length=1, max_length=128)

    google_api_key: SecretStr | None = None
    google_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    google_model: str = "gemini-3.6-flash"

    nvidia_api_key: SecretStr | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "nvidia/nemotron-3.5-nano-30b-a3b"

    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str | None = None

    anthropic_api_key: SecretStr | None = None
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    anthropic_model: str | None = None
    anthropic_version: str = "2023-06-01"

    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str | None = None

    seed_version: str = Field(default="e2e-v1", min_length=1, max_length=64)
    audit_retention_days: int = Field(default=90, ge=7, le=3_650)

    @field_validator("catalog_path")
    @classmethod
    def validate_catalog_path(cls, value: Path) -> Path:
        resolved_path = value.expanduser()
        if not resolved_path.is_absolute():
            raise ValueError("catalog_path must be an absolute path")
        if resolved_path.suffix.lower() not in {".yaml", ".yml"}:
            raise ValueError("catalog_path must reference a .yaml or .yml file")
        return resolved_path.resolve(strict=False)

    @field_validator(
        "neo4j_user",
        "valkey_host",
        "temporal_target",
        "sqlserver_host",
        "sqlserver_user",
        "sqlserver_database",
        "source_mongo_database",
        "ai_provider_order",
        "google_base_url",
        "nvidia_base_url",
        "openai_base_url",
        "anthropic_base_url",
        "ollama_base_url",
    )
    @classmethod
    def reject_blank_strings(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value must not be blank.")
        return (
            normalized.rstrip("/") if normalized.startswith(("http://", "https://")) else normalized
        )

    @field_validator(
        "google_api_key", "nvidia_api_key", "openai_api_key", "anthropic_api_key", mode="before"
    )
    @classmethod
    def normalize_optional_secrets(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("openai_model", "anthropic_model", "ollama_model", mode="before")
    @classmethod
    def normalize_optional_models(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("mongo_dsn", "neo4j_password", "valkey_password", "sqlserver_password")
    @classmethod
    def reject_blank_secrets(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Secret must not be blank.")
        return value

    @field_validator("mongo_dsn", "source_mongo_dsn")
    @classmethod
    def validate_mongo_dsn(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        raw_value = value.get_secret_value()
        if not raw_value.startswith(("mongodb://", "mongodb+srv://")):
            raise ValueError("MongoDB DSN must start with mongodb:// or mongodb+srv://.")
        return value

    @field_validator("neo4j_uri")
    @classmethod
    def validate_neo4j_uri(cls, value: str) -> str:
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
            raise ValueError("Neo4j URI uses an unsupported scheme.")
        return normalized

    @field_validator("frontend_cors_origin")
    @classmethod
    def validate_cors_origin(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.path not in ("", "/") or value.query or value.fragment:
            raise ValueError("CORS origin must contain only scheme, host, and optional port.")
        return value

    @field_validator("temporal_target")
    @classmethod
    def validate_temporal_target(cls, value: str) -> str:
        normalized = value.strip()
        if ":" not in normalized:
            raise ValueError("Temporal target must include host and port.")
        host, port_text = normalized.rsplit(":", maxsplit=1)
        if not host:
            raise ValueError("Temporal host must not be empty.")
        try:
            port = int(port_text)
        except ValueError as error:
            raise ValueError("Temporal port must be an integer.") from error
        if not 1 <= port <= 65_535:
            raise ValueError("Temporal port must be between 1 and 65535.")
        return normalized

    @field_validator("ai_provider_order")
    @classmethod
    def validate_provider_order(cls, value: str) -> str:
        allowed = {"GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC", "OLLAMA", "SIMULATOR"}
        providers = tuple(part.strip().upper() for part in value.split(",") if part.strip())
        if (
            not providers
            or len(set(providers)) != len(providers)
            or any(p not in allowed for p in providers)
        ):
            raise ValueError("ai_provider_order is invalid")
        return ",".join(providers)

    @model_validator(mode="after")
    def validate_relationships(self) -> Self:
        if self.dependency_connect_timeout_seconds < self.probe_timeout_seconds:
            raise ValueError("Connection timeout must be greater than or equal to probe timeout.")
        if self.ai_global_timeout_seconds < self.ai_timeout_seconds:
            raise ValueError("AI global timeout must be greater than or equal to provider timeout.")
        if self.environment == "production" and "SIMULATOR" in self.ai_provider_order.split(","):
            raise ValueError("SIMULATOR cannot be configured in production.")
        return self

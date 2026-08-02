import json
from pathlib import Path
from typing import Literal, Self

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[3]
REPOSITORY_ROOT = BACKEND_ROOT.parent
DEFAULT_DATA_ASSET_CATALOG_PATH = BACKEND_ROOT / "config" / "data_assets.yaml"
DEFAULT_SCHEMA_REGISTRY_PATH = BACKEND_ROOT / "config" / "schema_registry.yaml"
DEFAULT_RETURN_CONFIGURATION_PATH = BACKEND_ROOT / "config" / "returns" / "production.yaml"
DEFAULT_DEPENDENCY_SIMULATION_CONFIGURATION_PATH = (
    BACKEND_ROOT / "config" / "dependency_simulation.yaml"
)
DEFAULT_AI_GATEWAY_CONFIGURATION_PATH = BACKEND_ROOT / "config" / "ai_gateway.yaml"


class Settings(BaseSettings):
    """Validated process configuration. Secrets are never serialized by application APIs."""

    model_config = SettingsConfigDict(
        env_prefix="PLATFORM_",
        env_file=REPOSITORY_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        validate_default=True,
        frozen=True,
    )

    catalog_path: Path = Field(default=DEFAULT_DATA_ASSET_CATALOG_PATH)
    schema_registry_path: Path = Field(default=DEFAULT_SCHEMA_REGISTRY_PATH)
    return_configuration_path: Path = Field(default=DEFAULT_RETURN_CONFIGURATION_PATH)
    dependency_simulation_configuration_path: Path = Field(
        default=DEFAULT_DEPENDENCY_SIMULATION_CONFIGURATION_PATH
    )
    ai_gateway_configuration_path: Path = Field(default=DEFAULT_AI_GATEWAY_CONFIGURATION_PATH)
    environment: Literal["development", "test", "staging", "production"] = "development"

    vault_enabled: bool = False
    vault_address: str = "http://127.0.0.1:8201"
    vault_token: SecretStr | None = None
    vault_token_file: Path | None = None
    vault_namespace: str | None = None
    vault_verify_tls: bool = True
    vault_timeout_seconds: float = Field(default=5.0, gt=0.0, le=30.0)
    vault_secrets_resolved: bool = False
    mongo_dsn_secret_reference: str | None = None
    source_mongo_dsn_secret_reference: str | None = None
    neo4j_password_secret_reference: str | None = None
    valkey_password_secret_reference: str | None = None
    sqlserver_password_secret_reference: str | None = None
    google_api_key_references: tuple[str, ...] = ()
    nvidia_api_key_references: tuple[str, ...] = ()
    openai_api_key_references: tuple[str, ...] = ()
    anthropic_api_key_references: tuple[str, ...] = ()
    validation_fingerprint_key_secret_reference: str | None = None
    validation_fingerprint_key: SecretStr = SecretStr(
        "development-validation-fingerprint-key-change-me"
    )
    contact_lookup_hmac_key_secret_reference: str | None = None
    contact_lookup_hmac_key: SecretStr = SecretStr("development-contact-lookup-hmac-key-change-me")

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
    graph_sync_batch_size: int = Field(default=250, ge=1, le=5_000)
    graph_sync_max_records: int = Field(default=10_000, ge=1, le=1_000_000)

    ai_provider_order: str = "GOOGLE,NVIDIA,SIMULATOR"
    ai_timeout_seconds: float = Field(default=12.0, ge=0.5, le=60.0)
    ai_global_timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)
    ai_max_attempts_per_provider: int = Field(default=2, ge=1, le=4)
    ai_max_concurrency: int = Field(default=16, ge=1, le=256)
    ai_requests_per_minute: int = Field(default=120, ge=1, le=100_000)
    ai_max_payload_bytes: int = Field(default=16_384, ge=1_024, le=1_048_576)
    ai_interception_default: bool = False
    ai_prompt_version: str = Field(default="return-eligibility-v1", min_length=1, max_length=128)
    ai_allowed_endpoint_hosts: tuple[str, ...] = (
        "generativelanguage.googleapis.com",
        "integrate.api.nvidia.com",
        "api.openai.com",
        "api.anthropic.com",
    )
    data_source_allowed_hosts: tuple[str, ...] = (
        "mongodb",
        "source-mongodb",
        "sqlserver",
        "neo4j",
        "localhost",
        "127.0.0.1",
        "::1",
    )
    data_source_credential_reveal_enabled: bool = False

    # Credential and model pools. List fields are the production path; single-value fields
    # remain as backward-compatible fallbacks during migration.
    google_api_keys: tuple[SecretStr, ...] = ()
    google_lightweight_models: tuple[str, ...] = ()
    google_standard_models: tuple[str, ...] = ()
    google_api_key: SecretStr | None = None
    google_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    google_model: str | None = None

    nvidia_api_keys: tuple[SecretStr, ...] = ()
    nvidia_lightweight_models: tuple[str, ...] = ()
    nvidia_standard_models: tuple[str, ...] = ()
    nvidia_api_key: SecretStr | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str | None = None

    openai_api_keys: tuple[SecretStr, ...] = ()
    openai_lightweight_models: tuple[str, ...] = ()
    openai_standard_models: tuple[str, ...] = ()
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str | None = None

    anthropic_api_keys: tuple[SecretStr, ...] = ()
    anthropic_lightweight_models: tuple[str, ...] = ()
    anthropic_standard_models: tuple[str, ...] = ()
    anthropic_api_key: SecretStr | None = None
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    anthropic_model: str | None = None
    anthropic_version: str = "2023-06-01"

    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_lightweight_models: tuple[str, ...] = ()
    ollama_standard_models: tuple[str, ...] = ()
    ollama_model: str | None = None

    seed_version: str = Field(default="e2e-v1", min_length=1, max_length=64)
    ai_studio_max_records: int = Field(default=500, ge=1, le=10_000)
    # AI Studio write targets are deliberately separate from operational/source stores.
    # Apply remains disabled until an operator supplies all isolated validation coordinates.
    ai_studio_mongo_database: str | None = None
    ai_studio_sqlserver_host: str | None = None
    ai_studio_sqlserver_port: int = Field(default=1433, ge=1, le=65_535)
    ai_studio_sqlserver_user: str | None = None
    ai_studio_sqlserver_password: SecretStr | None = None
    ai_studio_sqlserver_database: str | None = None
    support_ticket_mode: Literal[
        "INTERNAL", "INTERNAL_WITH_EXTERNAL_MIRROR", "EXTERNAL_AUTHORITY"
    ] = "INTERNAL"
    support_ticket_base_url: str | None = None
    support_ticket_api_key: SecretStr | None = None
    support_ticket_timeout_seconds: float = Field(default=15.0, ge=1.0, le=120.0)
    support_ticket_poll_seconds: float = Field(default=5.0, ge=0.5, le=300.0)
    support_ticket_max_polls: int = Field(default=12, ge=1, le=120)
    omc_command_base_url: str | None = None
    omc_command_api_key: SecretStr | None = None
    carrier_booking_base_url: str | None = None
    carrier_booking_api_key: SecretStr | None = None
    customer_notification_base_url: str | None = None
    customer_notification_api_key: SecretStr | None = None

    omc_dependency_mode: Literal["REAL", "SIMULATED", "MANUAL", "BLOCKED"] = "SIMULATED"
    parcel_dependency_mode: Literal["REAL", "SIMULATED", "MANUAL", "BLOCKED"] = "SIMULATED"
    freight_dependency_mode: Literal["REAL", "SIMULATED", "MANUAL", "BLOCKED"] = "SIMULATED"
    lsi_dependency_mode: Literal["REAL", "SIMULATED", "MANUAL", "BLOCKED"] = "SIMULATED"
    feedback_learning_enabled: bool = True
    audit_retention_days: int = Field(default=90, ge=7, le=3_650)

    @field_validator(
        "catalog_path",
        "schema_registry_path",
        "return_configuration_path",
        "dependency_simulation_configuration_path",
        "ai_gateway_configuration_path",
    )
    @classmethod
    def validate_catalog_path(cls, value: Path) -> Path:
        resolved_path = value.expanduser()
        if not resolved_path.is_absolute():
            raise ValueError("Configuration registry paths must be absolute.")
        if resolved_path.suffix.lower() not in {".yaml", ".yml"}:
            raise ValueError("Configuration registry paths must reference YAML files.")
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
        "google_api_keys",
        "nvidia_api_keys",
        "openai_api_keys",
        "anthropic_api_keys",
        mode="before",
    )
    @classmethod
    def parse_secret_list(cls, value: object) -> object:
        if value is None or value == "":
            return ()
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return ()
            if raw.startswith("["):
                parsed = json.loads(raw)
                if not isinstance(parsed, list):
                    raise ValueError("AI credential setting must contain a JSON list.")
                return tuple(str(item).strip() for item in parsed if str(item).strip())
            return tuple(item.strip() for item in raw.split(",") if item.strip())
        if isinstance(value, (list, tuple)):
            return tuple(value)
        raise ValueError("AI credential setting must be a JSON list or comma-separated string.")

    @field_validator(
        "google_api_key_references",
        "nvidia_api_key_references",
        "openai_api_key_references",
        "anthropic_api_key_references",
        mode="before",
    )
    @classmethod
    def parse_reference_list(cls, value: object) -> object:
        if value is None or value == "":
            return ()
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return ()
            if raw.startswith("["):
                parsed = json.loads(raw)
                if not isinstance(parsed, list):
                    raise ValueError("Vault reference setting must contain a JSON list.")
                values = [str(item).strip() for item in parsed if str(item).strip()]
            else:
                values = [item.strip() for item in raw.split(",") if item.strip()]
        elif isinstance(value, (list, tuple)):
            values = [str(item).strip() for item in value if str(item).strip()]
        else:
            raise ValueError(
                "Vault reference setting must be a JSON list or comma-separated string."
            )
        if len(values) != len(set(values)):
            raise ValueError("Vault reference lists must not contain duplicates.")
        return tuple(values)

    @field_validator("ai_allowed_endpoint_hosts", mode="before")
    @classmethod
    def parse_allowed_ai_hosts(cls, value: object) -> object:
        if value is None or value == "":
            return ()
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return ()
            if raw.startswith("["):
                parsed = json.loads(raw)
                if not isinstance(parsed, list):
                    raise ValueError("AI endpoint host allowlist must contain a JSON list.")
                values = [str(item).strip().lower() for item in parsed if str(item).strip()]
            else:
                values = [item.strip().lower() for item in raw.split(",") if item.strip()]
        elif isinstance(value, (list, tuple)):
            values = [str(item).strip().lower() for item in value if str(item).strip()]
        else:
            raise ValueError(
                "AI endpoint host allowlist must be a JSON list or comma-separated string."
            )
        if len(values) != len(set(values)):
            raise ValueError("AI endpoint host allowlist must not contain duplicates.")
        return tuple(values)

    @field_validator("data_source_allowed_hosts", mode="before")
    @classmethod
    def parse_allowed_data_source_hosts(cls, value: object) -> object:
        if value is None or value == "":
            return ()
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return ()
            if raw.startswith("["):
                parsed = json.loads(raw)
                if not isinstance(parsed, list):
                    raise ValueError("Data-source host allowlist must contain a JSON list.")
                values = [str(item).strip().lower() for item in parsed if str(item).strip()]
            else:
                values = [item.strip().lower() for item in raw.split(",") if item.strip()]
        elif isinstance(value, (list, tuple)):
            values = [str(item).strip().lower() for item in value if str(item).strip()]
        else:
            raise ValueError(
                "Data-source host allowlist must be a JSON list or comma-separated string."
            )
        if len(values) != len(set(values)):
            raise ValueError("Data-source host allowlist must not contain duplicates.")
        return tuple(values)

    @field_validator(
        "google_lightweight_models",
        "google_standard_models",
        "nvidia_lightweight_models",
        "nvidia_standard_models",
        "openai_lightweight_models",
        "openai_standard_models",
        "anthropic_lightweight_models",
        "anthropic_standard_models",
        "ollama_lightweight_models",
        "ollama_standard_models",
        mode="before",
    )
    @classmethod
    def parse_model_list(cls, value: object) -> object:
        if value is None or value == "":
            return ()
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return ()
            if raw.startswith("["):
                parsed = json.loads(raw)
                if not isinstance(parsed, list):
                    raise ValueError("AI model setting must contain a JSON list.")
                values = [str(item).strip() for item in parsed if str(item).strip()]
            else:
                values = [item.strip() for item in raw.split(",") if item.strip()]
        elif isinstance(value, (list, tuple)):
            values = [str(item).strip() for item in value if str(item).strip()]
        else:
            raise ValueError("AI model setting must be a JSON list or comma-separated string.")
        if len(values) != len(set(values)):
            raise ValueError("AI model lists must not contain duplicates.")
        return tuple(values)

    @field_validator(
        "google_api_key",
        "nvidia_api_key",
        "openai_api_key",
        "anthropic_api_key",
        "support_ticket_api_key",
        "omc_command_api_key",
        "carrier_booking_api_key",
        "customer_notification_api_key",
        mode="before",
    )
    @classmethod
    def normalize_optional_secrets(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "google_model",
        "nvidia_model",
        "openai_model",
        "anthropic_model",
        "ollama_model",
        mode="before",
    )
    @classmethod
    def normalize_optional_models(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "omc_dependency_mode",
        "parcel_dependency_mode",
        "freight_dependency_mode",
        "lsi_dependency_mode",
        mode="before",
    )
    @classmethod
    def normalize_dependency_mode(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("support_ticket_mode", mode="before")
    @classmethod
    def normalize_support_ticket_mode(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().upper()
            aliases = {
                "EXTERNAL": "EXTERNAL_AUTHORITY",
            }
            return aliases.get(normalized, normalized)
        return value

    @field_validator(
        "support_ticket_base_url",
        "omc_command_base_url",
        "carrier_booking_base_url",
        "customer_notification_base_url",
        mode="before",
    )
    @classmethod
    def normalize_optional_support_url(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            if not normalized.startswith(("http://", "https://")):
                raise ValueError("External integration base URL must use HTTP or HTTPS.")
            return normalized.rstrip("/")
        return value

    @field_validator("mongo_dsn", "neo4j_password", "valkey_password", "sqlserver_password")
    @classmethod
    def reject_blank_secrets(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Secret must not be blank.")
        return value

    @field_validator("ai_studio_sqlserver_password", mode="before")
    @classmethod
    def normalize_blank_optional_secret(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            return value if value.get_secret_value().strip() else None
        if isinstance(value, str):
            return value if value.strip() else None
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
        allowed = {
            "GOOGLE",
            "NVIDIA",
            "OPENAI",
            "ANTHROPIC",
            "OLLAMA",
            "SIMULATOR",
        }
        providers = tuple(part.strip().upper() for part in value.split(",") if part.strip())
        if providers == ("NONE",):
            return "NONE"
        if (
            not providers
            or len(set(providers)) != len(providers)
            or any(p not in allowed for p in providers)
        ):
            raise ValueError("ai_provider_order is invalid")
        return ",".join(providers)

    @property
    def resolved_google_api_keys(self) -> tuple[SecretStr, ...]:
        return self.google_api_keys or ((self.google_api_key,) if self.google_api_key else ())

    @property
    def resolved_nvidia_api_keys(self) -> tuple[SecretStr, ...]:
        return self.nvidia_api_keys or ((self.nvidia_api_key,) if self.nvidia_api_key else ())

    @property
    def resolved_openai_api_keys(self) -> tuple[SecretStr, ...]:
        return self.openai_api_keys or ((self.openai_api_key,) if self.openai_api_key else ())

    @property
    def resolved_anthropic_api_keys(self) -> tuple[SecretStr, ...]:
        return self.anthropic_api_keys or (
            (self.anthropic_api_key,) if self.anthropic_api_key else ()
        )

    @property
    def resolved_google_standard_models(self) -> tuple[str, ...]:
        return self.google_standard_models or ((self.google_model,) if self.google_model else ())

    @property
    def resolved_nvidia_standard_models(self) -> tuple[str, ...]:
        return self.nvidia_standard_models or ((self.nvidia_model,) if self.nvidia_model else ())

    @property
    def resolved_openai_standard_models(self) -> tuple[str, ...]:
        return self.openai_standard_models or ((self.openai_model,) if self.openai_model else ())

    @property
    def resolved_anthropic_standard_models(self) -> tuple[str, ...]:
        return self.anthropic_standard_models or (
            (self.anthropic_model,) if self.anthropic_model else ()
        )

    @property
    def resolved_ollama_standard_models(self) -> tuple[str, ...]:
        return self.ollama_standard_models or ((self.ollama_model,) if self.ollama_model else ())

    @model_validator(mode="after")
    def validate_relationships(self) -> Self:
        if self.dependency_connect_timeout_seconds < self.probe_timeout_seconds:
            raise ValueError("Connection timeout must be greater than or equal to probe timeout.")
        if self.ai_global_timeout_seconds < self.ai_timeout_seconds:
            raise ValueError("AI global timeout must be greater than or equal to provider timeout.")
        if self.environment == "production" and "SIMULATOR" in self.ai_provider_order.split(","):
            raise ValueError("SIMULATOR cannot be configured in production.")
        dependency_modes = {
            self.omc_dependency_mode,
            self.parcel_dependency_mode,
            self.freight_dependency_mode,
            self.lsi_dependency_mode,
        }
        if self.environment == "production" and "SIMULATED" in dependency_modes:
            raise ValueError("External dependency simulation is forbidden in production.")
        if (
            self.support_ticket_mode in {"INTERNAL_WITH_EXTERNAL_MIRROR", "EXTERNAL_AUTHORITY"}
            and self.support_ticket_base_url is None
        ):
            raise ValueError("Configured external Return Support integration requires a base URL.")
        if self.environment == "production":
            if not self.vault_enabled:
                raise ValueError("Vault must be enabled in production.")
            required_references = {
                "mongo_dsn_secret_reference": self.mongo_dsn_secret_reference,
                "source_mongo_dsn_secret_reference": self.source_mongo_dsn_secret_reference,
                "neo4j_password_secret_reference": self.neo4j_password_secret_reference,
                "valkey_password_secret_reference": self.valkey_password_secret_reference,
                "sqlserver_password_secret_reference": self.sqlserver_password_secret_reference,
                "validation_fingerprint_key_secret_reference": (
                    self.validation_fingerprint_key_secret_reference
                ),
                "contact_lookup_hmac_key_secret_reference": (
                    self.contact_lookup_hmac_key_secret_reference
                ),
            }
            missing = sorted(name for name, value in required_references.items() if not value)
            if missing:
                raise ValueError("Production Vault references are missing: " + ", ".join(missing))
            if self.vault_secrets_resolved:
                if self.validation_fingerprint_key.get_secret_value().endswith("change-me"):
                    raise ValueError("Production validation fingerprint key must be replaced.")
                if self.contact_lookup_hmac_key.get_secret_value().endswith("change-me"):
                    raise ValueError("Production contact lookup HMAC key must be replaced.")
            hosted_ai_keys_present = any(
                (
                    self.google_api_keys,
                    self.nvidia_api_keys,
                    self.openai_api_keys,
                    self.anthropic_api_keys,
                    (self.google_api_key,) if self.google_api_key else (),
                    (self.nvidia_api_key,) if self.nvidia_api_key else (),
                    (self.openai_api_key,) if self.openai_api_key else (),
                    (self.anthropic_api_key,) if self.anthropic_api_key else (),
                )
            )
            if hosted_ai_keys_present and not self.vault_secrets_resolved:
                raise ValueError(
                    "Production AI keys must be resolved from validated Vault references"
                )
        return self

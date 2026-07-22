"""CLI entry point for live sandbox-only Customer graph validation."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import stat
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Final, Never, TextIO
from uuid import uuid4

from neo4j import AsyncGraphDatabase
from neo4j.exceptions import DriverError, Neo4jError
from pydantic import (
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from return_platform.data_platform.graph.readback import (
    CustomerGraphReadbackError,
    CustomerGraphReadbackValidator,
)
from return_platform.data_platform.graph.sandbox import (
    CustomerGraphSandboxError,
    CustomerGraphSandboxErrorCode,
    CustomerGraphSandboxExecutor,
    CustomerGraphSandboxReport,
    CustomerGraphSandboxService,
)
from return_platform.data_platform.graph.writer import (
    CustomerNeo4jWriter,
    Neo4jWriteError,
)
from return_platform.data_platform.mapping import SourceDocumentEvidence

__all__ = [
    "CustomerGraphSandboxSettings",
    "LoadedCustomerGraphSourceDocument",
    "SandboxRunnerError",
    "SandboxRunnerErrorCode",
    "load_customer_graph_source_document",
    "main",
]

_MAX_SOURCE_FILE_BYTES: Final = 1_048_576
_DEFAULT_CONFIG_DIR: Final = Path("config/data_platform")
_DEFAULT_EVIDENCE_FILE: Final = Path("docs/evidence/customer_graph_sandbox_validation.json")


class SandboxExitCode(IntEnum):
    """Stable process exit codes for automation."""

    SUCCESS = 0
    INPUT_OR_CONFIGURATION = 2
    MAPPING_OR_MATERIALIZATION = 3
    NEO4J_WRITE = 4
    READBACK_OR_IDEMPOTENCY = 5
    CONNECTIVITY = 6


class SandboxRunnerErrorCode(StrEnum):
    """Stable safe runner-boundary error codes."""

    INPUT_INVALID = "INPUT_INVALID"
    SOURCE_FILE_INVALID = "SOURCE_FILE_INVALID"
    SOURCE_JSON_INVALID = "SOURCE_JSON_INVALID"
    SOURCE_UPDATED_AT_INVALID = "SOURCE_UPDATED_AT_INVALID"
    EVIDENCE_OUTPUT_INVALID = "EVIDENCE_OUTPUT_INVALID"
    CONNECTIVITY_FAILED = "CONNECTIVITY_FAILED"


class SandboxRunnerError(RuntimeError):
    """Safe runner error with no raw source or secret content."""

    def __init__(self, code: SandboxRunnerErrorCode) -> None:
        """Initialize one safe runner error."""
        self.code = code
        super().__init__(code.value)


def _raise_runner_error(code: SandboxRunnerErrorCode) -> Never:
    """Raise one safe runner error."""
    raise SandboxRunnerError(code)


class CustomerGraphSandboxSettings(BaseSettings):
    """Strict environment-only Neo4j sandbox settings."""

    model_config = SettingsConfigDict(
        env_prefix="PLATFORM_",
        extra="ignore",
        frozen=True,
        strict=True,
        validate_default=True,
    )

    neo4j_uri: str
    neo4j_user: str
    neo4j_password: SecretStr
    neo4j_database: str = "neo4j"
    neo4j_connectivity_timeout_seconds: float = Field(
        default=10.0,
        strict=True,
        ge=0.05,
        le=60.0,
        allow_inf_nan=False,
    )
    neo4j_transaction_timeout_seconds: float = Field(
        default=5.0,
        strict=True,
        ge=0.05,
        le=300.0,
        allow_inf_nan=False,
    )
    neo4j_operation_timeout_seconds: float = Field(
        default=10.0,
        strict=True,
        ge=0.05,
        le=600.0,
        allow_inf_nan=False,
    )

    @field_validator("neo4j_uri")
    @classmethod
    def validate_uri(cls, value: str) -> str:
        """Allow only explicit Neo4j URI schemes without surrounding space."""
        if value.strip() != value or not value.startswith(
            ("neo4j://", "neo4j+s://", "bolt://", "bolt+s://")
        ):
            msg = "neo4j_uri must use an approved explicit scheme"
            raise ValueError(msg)
        return value

    @field_validator("neo4j_user", "neo4j_database")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        """Reject blank or whitespace-normalized identifiers."""
        if not value or value.strip() != value:
            msg = "identifier must be non-blank and already normalized"
            raise ValueError(msg)
        return value

    def model_post_init(self, context: object) -> None:
        """Require operation timeout to exceed transaction timeout."""
        del context
        if self.neo4j_operation_timeout_seconds <= self.neo4j_transaction_timeout_seconds:
            msg = "operation timeout must exceed transaction timeout"
            raise ValueError(msg)


class LoadedCustomerGraphSourceDocument:
    """Bounded source file payload and exact byte digest."""

    __slots__ = ("document", "source_hash")

    document: dict[str, object]
    source_hash: str

    def __init__(self, document: dict[str, object], source_hash: str) -> None:
        """Store a detached source object and its exact byte digest."""
        self.document = document
        self.source_hash = source_hash


def _reject_duplicate_object_pairs(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """Reject duplicate JSON keys at every object depth."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _raise_runner_error(SandboxRunnerErrorCode.SOURCE_JSON_INVALID)
        result[key] = value
    return result


def _reject_non_finite_json(value: str) -> Never:
    """Reject NaN and infinity JSON extensions."""
    del value
    _raise_runner_error(SandboxRunnerErrorCode.SOURCE_JSON_INVALID)


def load_customer_graph_source_document(
    path: Path,
) -> LoadedCustomerGraphSourceDocument:
    """Load one regular bounded UTF-8 JSON object with duplicate-key rejection."""
    try:
        file_stat = path.lstat()
    except OSError as error:
        raise SandboxRunnerError(SandboxRunnerErrorCode.SOURCE_FILE_INVALID) from error
    if (
        stat.S_ISLNK(file_stat.st_mode)
        or not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_size <= 0
        or file_stat.st_size > _MAX_SOURCE_FILE_BYTES
    ):
        _raise_runner_error(SandboxRunnerErrorCode.SOURCE_FILE_INVALID)
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig", errors="strict")
        parsed: object = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_object_pairs,
            parse_constant=_reject_non_finite_json,
        )
    except UnicodeDecodeError as error:
        raise SandboxRunnerError(SandboxRunnerErrorCode.SOURCE_JSON_INVALID) from error
    except (json.JSONDecodeError, RecursionError, ValueError) as error:
        raise SandboxRunnerError(SandboxRunnerErrorCode.SOURCE_JSON_INVALID) from error
    except OSError as error:
        raise SandboxRunnerError(SandboxRunnerErrorCode.SOURCE_FILE_INVALID) from error
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        _raise_runner_error(SandboxRunnerErrorCode.SOURCE_JSON_INVALID)
    document = {str(key): value for key, value in parsed.items()}
    return LoadedCustomerGraphSourceDocument(
        document,
        hashlib.sha256(raw).hexdigest(),
    )


def _parse_utc_datetime(value: str) -> datetime:
    """Parse one explicit ISO-8601 UTC timestamp without local-time inference."""
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise SandboxRunnerError(SandboxRunnerErrorCode.SOURCE_UPDATED_AT_INVALID) from error
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        _raise_runner_error(SandboxRunnerErrorCode.SOURCE_UPDATED_AT_INVALID)
    return parsed.astimezone(UTC)


def _write_json_line(stream: TextIO, payload: dict[str, object]) -> None:
    """Write one compact deterministic JSON line to stdout or stderr."""
    text = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    stream.write(text + "\n")
    stream.flush()


def _emit_failure(
    code: StrEnum,
    exit_code: SandboxExitCode,
) -> int:
    """Emit one safe machine-readable failure record."""
    _write_json_line(
        sys.stderr,
        {
            "status": "FAILED",
            "error_code": code.value,
            "process_exit_code": int(exit_code),
        },
    )
    return int(exit_code)


def _write_evidence_atomically(
    output_path: Path,
    report: CustomerGraphSandboxReport,
) -> None:
    """Persist one complete report by fsync and atomic replace."""
    temporary_name: str | None = None
    replaced = False
    try:
        if output_path.is_symlink():
            _raise_runner_error(SandboxRunnerErrorCode.EVIDENCE_OUTPUT_INVALID)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = report.model_dump_json(indent=2).encode("utf-8") + b"\n"
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            dir=output_path.parent,
        )
        os.fchmod(file_descriptor, 0o600)
        with os.fdopen(file_descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output_path)
        replaced = True
    except SandboxRunnerError:
        raise
    except OSError as error:
        raise SandboxRunnerError(SandboxRunnerErrorCode.EVIDENCE_OUTPUT_INVALID) from error
    finally:
        if temporary_name is not None and not replaced:
            with suppress(OSError):
                Path(temporary_name).unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    """Build the deterministic sandbox command-line parser."""
    parser = argparse.ArgumentParser(
        prog="customer-graph-sandbox-validator",
        description=("Validate one controlled Customer document against a Neo4j sandbox."),
    )
    parser.add_argument("--source-file", type=Path, required=True)
    parser.add_argument("--source-document-id", required=True)
    parser.add_argument("--source-updated-at", required=True)
    parser.add_argument("--source-version")
    parser.add_argument("--source-event-id")
    parser.add_argument("--config-dir", type=Path, default=_DEFAULT_CONFIG_DIR)
    parser.add_argument(
        "--evidence-output",
        type=Path,
        default=_DEFAULT_EVIDENCE_FILE,
    )
    return parser


@dataclass(frozen=True, slots=True)
class _Arguments:
    """Strict command-line arguments after argparse boundary validation."""

    source_file: Path
    source_document_id: str
    source_updated_at: str
    source_version: str | None
    source_event_id: str | None
    config_dir: Path
    evidence_output: Path


class _ArgumentNamespace(argparse.Namespace):
    """Typed mutable namespace populated only by argparse."""

    source_file: Path | None
    source_document_id: str | None
    source_updated_at: str | None
    source_version: str | None
    source_event_id: str | None
    config_dir: Path
    evidence_output: Path

    def __init__(self) -> None:
        """Initialize required values as absent and defaults as concrete paths."""
        super().__init__()
        self.source_file = None
        self.source_document_id = None
        self.source_updated_at = None
        self.source_version = None
        self.source_event_id = None
        self.config_dir = _DEFAULT_CONFIG_DIR
        self.evidence_output = _DEFAULT_EVIDENCE_FILE


def _parse_arguments(argv: list[str] | None) -> _Arguments:
    """Convert argparse output into one strictly typed immutable contract."""
    namespace = _ArgumentNamespace()
    _parser().parse_args(argv, namespace=namespace)
    if (
        namespace.source_file is None
        or namespace.source_document_id is None
        or not namespace.source_document_id
        or namespace.source_document_id.strip() != namespace.source_document_id
        or namespace.source_updated_at is None
        or (namespace.source_version is not None and not namespace.source_version)
        or (namespace.source_event_id is not None and not namespace.source_event_id)
    ):
        _raise_runner_error(SandboxRunnerErrorCode.INPUT_INVALID)
    return _Arguments(
        source_file=namespace.source_file,
        source_document_id=namespace.source_document_id,
        source_updated_at=namespace.source_updated_at,
        source_version=namespace.source_version,
        source_event_id=namespace.source_event_id,
        config_dir=namespace.config_dir,
        evidence_output=namespace.evidence_output,
    )


async def _run(
    *,
    settings: CustomerGraphSandboxSettings,
    args: _Arguments,
) -> CustomerGraphSandboxReport:
    """Own the live driver and execute one complete sandbox validation run."""
    source = load_customer_graph_source_document(args.source_file)
    source_updated_at = _parse_utc_datetime(args.source_updated_at)
    observed_at = datetime.now(UTC)
    source_evidence = SourceDocumentEvidence(
        source_document_id=args.source_document_id,
        source_updated_at=source_updated_at,
        source_version=args.source_version,
        source_event_id=args.source_event_id,
        source_hash=source.source_hash,
        observed_at=observed_at,
    )
    try:
        driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(
                settings.neo4j_user,
                settings.neo4j_password.get_secret_value(),
            ),
            connection_timeout=settings.neo4j_connectivity_timeout_seconds,
            connection_acquisition_timeout=(settings.neo4j_connectivity_timeout_seconds),
            max_connection_pool_size=4,
            disable_auto_commit_retries=True,
        )
    except (DriverError, Neo4jError, ValueError) as error:
        raise SandboxRunnerError(SandboxRunnerErrorCode.CONNECTIVITY_FAILED) from error
    try:
        try:
            async with asyncio.timeout(settings.neo4j_connectivity_timeout_seconds):
                await driver.verify_connectivity()
        except (TimeoutError, DriverError, Neo4jError) as error:
            raise SandboxRunnerError(SandboxRunnerErrorCode.CONNECTIVITY_FAILED) from error
        writer = CustomerNeo4jWriter(driver)
        readback = CustomerGraphReadbackValidator(driver)
        executor = CustomerGraphSandboxExecutor(writer, readback)
        service = CustomerGraphSandboxService(executor)
        return await service.validate(
            config_dir=args.config_dir,
            source_document=source.document,
            source_evidence=source_evidence,
            source_hash=source.source_hash,
            sync_run_id=uuid4(),
            graph_synced_at=datetime.now(UTC),
            database=settings.neo4j_database,
            transaction_timeout_seconds=(settings.neo4j_transaction_timeout_seconds),
            operation_timeout_seconds=settings.neo4j_operation_timeout_seconds,
        )
    finally:
        await driver.close()


def main(argv: list[str] | None = None) -> int:
    """Execute the sandbox validator and return a stable process exit code."""
    try:
        args = _parse_arguments(argv)
        settings = CustomerGraphSandboxSettings()
        report = asyncio.run(_run(settings=settings, args=args))
        _write_evidence_atomically(args.evidence_output, report)
    except ValidationError:
        return _emit_failure(
            SandboxRunnerErrorCode.INPUT_INVALID,
            SandboxExitCode.INPUT_OR_CONFIGURATION,
        )
    except SandboxRunnerError as error:
        exit_code = (
            SandboxExitCode.CONNECTIVITY
            if error.code is SandboxRunnerErrorCode.CONNECTIVITY_FAILED
            else SandboxExitCode.INPUT_OR_CONFIGURATION
        )
        return _emit_failure(error.code, exit_code)
    except CustomerGraphSandboxError as error:
        exit_code = (
            SandboxExitCode.READBACK_OR_IDEMPOTENCY
            if error.code is CustomerGraphSandboxErrorCode.EXECUTION_EVIDENCE_INVALID
            else SandboxExitCode.MAPPING_OR_MATERIALIZATION
        )
        return _emit_failure(error.code, exit_code)
    except Neo4jWriteError as error:
        return _emit_failure(error.code, SandboxExitCode.NEO4J_WRITE)
    except CustomerGraphReadbackError as error:
        return _emit_failure(
            error.code,
            SandboxExitCode.READBACK_OR_IDEMPOTENCY,
        )
    except (DriverError, Neo4jError):
        return _emit_failure(
            SandboxRunnerErrorCode.CONNECTIVITY_FAILED,
            SandboxExitCode.CONNECTIVITY,
        )
    _write_json_line(
        sys.stdout,
        {
            "status": "SANDBOX_VALIDATED",
            "process_exit_code": 0,
            "evidence_output": str(args.evidence_output),
            "report_digest": report.report_digest,
        },
    )
    return int(SandboxExitCode.SUCCESS)


if __name__ == "__main__":
    raise SystemExit(main())

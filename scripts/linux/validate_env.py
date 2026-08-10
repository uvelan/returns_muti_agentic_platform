#!/usr/bin/env python3
"""Validate a dotenv file without printing or expanding secret values."""

from __future__ import annotations

import argparse
import json
import re
import stat
from dataclasses import dataclass
from pathlib import Path

ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
PLACEHOLDER_MARKERS = (
    "placeholder",
    "replace-me",
    "change-me",
    "changeme",
    "secret-key",
    "your-key",
)
REQUIRED_SIMULATION_VALUES = {
    "PLATFORM_ENVIRONMENT": "development",
    "PLATFORM_OMC_DEPENDENCY_MODE": "SIMULATED",
    "PLATFORM_PARCEL_DEPENDENCY_MODE": "SIMULATED",
    "PLATFORM_FREIGHT_DEPENDENCY_MODE": "SIMULATED",
    "PLATFORM_LSI_DEPENDENCY_MODE": "SIMULATED",
}
REQUIRED_VAULT_VALUES = {
    "PLATFORM_VAULT_ENABLED": "true",
}
REQUIRED_VAULT_REFERENCES = (
    "PLATFORM_MONGO_DSN_SECRET_REFERENCE",
    "PLATFORM_SOURCE_MONGO_DSN_SECRET_REFERENCE",
    "PLATFORM_NEO4J_PASSWORD_SECRET_REFERENCE",
    "PLATFORM_VALKEY_PASSWORD_SECRET_REFERENCE",
    "PLATFORM_SQLSERVER_PASSWORD_SECRET_REFERENCE",
    "PLATFORM_VALIDATION_FINGERPRINT_KEY_SECRET_REFERENCE",
    "PLATFORM_CONTACT_LOOKUP_HMAC_KEY_SECRET_REFERENCE",
)
TEMPLATE_JSON_LISTS = (
    "PLATFORM_AI_ALLOWED_ENDPOINT_HOSTS",
    "PLATFORM_DATA_SOURCE_ALLOWED_HOSTS",
    "PLATFORM_GOOGLE_API_KEYS",
    "PLATFORM_GOOGLE_LIGHTWEIGHT_MODELS",
    "PLATFORM_GOOGLE_STANDARD_MODELS",
    "PLATFORM_NVIDIA_API_KEYS",
    "PLATFORM_NVIDIA_LIGHTWEIGHT_MODELS",
    "PLATFORM_NVIDIA_STANDARD_MODELS",
    "PLATFORM_OPENAI_API_KEYS",
    "PLATFORM_OPENAI_LIGHTWEIGHT_MODELS",
    "PLATFORM_OPENAI_STANDARD_MODELS",
    "PLATFORM_ANTHROPIC_API_KEYS",
    "PLATFORM_ANTHROPIC_LIGHTWEIGHT_MODELS",
    "PLATFORM_ANTHROPIC_STANDARD_MODELS",
    "PLATFORM_OLLAMA_LIGHTWEIGHT_MODELS",
    "PLATFORM_OLLAMA_STANDARD_MODELS",
)


@dataclass(frozen=True)
class ParsedValue:
    raw: str
    value: str
    single_quoted: bool


def _normalize(raw_value: str) -> ParsedValue:
    raw = raw_value.strip()
    single_quoted = len(raw) >= 2 and raw[0] == raw[-1] == "'"
    double_quoted = len(raw) >= 2 and raw[0] == raw[-1] == '"'
    value = raw[1:-1] if single_quoted or double_quoted else raw
    return ParsedValue(raw=raw, value=value, single_quoted=single_quoted)


def _parse(path: Path) -> dict[str, ParsedValue]:
    values: dict[str, ParsedValue] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = ASSIGNMENT.fullmatch(stripped)
        if match is None:
            raise ValueError(f"line {line_number} is not a dotenv assignment")
        name, raw_value = match.groups()
        parsed = _normalize(raw_value)
        if any(token in parsed.raw for token in ("$(", "`", "<(", ">(")):
            raise ValueError(f"{name} contains executable shell syntax")
        if name in values:
            raise ValueError(f"{name} is assigned more than once")
        values[name] = parsed
    return values


def _validate_permissions(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ValueError(
            ".env must not be accessible by group/other; run chmod 600 .env"
        )


def _validate_non_placeholder(name: str, value: str) -> None:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError(f"{name} is empty")
    if any(marker in normalized for marker in PLACEHOLDER_MARKERS):
        raise ValueError(f"{name} still contains a placeholder value")


def _parse_json_list(
    name: str, parsed_value: ParsedValue, *, require_non_empty: bool
) -> list[str]:
    if not parsed_value.single_quoted:
        raise ValueError(f"{name} must wrap its JSON array in single quotes")
    try:
        parsed = json.loads(parsed_value.value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be a valid JSON array") from exc
    if not isinstance(parsed, list) or (require_non_empty and not parsed):
        qualifier = "non-empty " if require_non_empty else ""
        raise ValueError(f"{name} must be a {qualifier}JSON array")
    if any(not isinstance(item, str) or not item.strip() for item in parsed):
        raise ValueError(f"{name} must contain only non-empty strings")
    return parsed


def validate(path: Path, *, simulation: bool, template_mode: bool = False) -> int:
    if not path.is_file():
        raise ValueError(f"dotenv file is missing: {path}")
    if not template_mode:
        _validate_permissions(path)
    values = _parse(path)

    if template_mode:
        for name in TEMPLATE_JSON_LISTS:
            parsed_value = values.get(name)
            if parsed_value is None:
                raise ValueError(f"{name} is missing")
            _parse_json_list(name, parsed_value, require_non_empty=False)
        for name in ("PLATFORM_MONGO_DSN", "PLATFORM_SOURCE_MONGO_DSN"):
            parsed_value = values.get(name)
            if parsed_value is None or not parsed_value.single_quoted:
                raise ValueError(f"{name} must be single-quoted")

    if simulation:
        for name, expected in REQUIRED_SIMULATION_VALUES.items():
            observed = values.get(name)
            if observed is None or observed.value != expected:
                raise ValueError(f"{name} must equal {expected}")

    for name, expected in REQUIRED_VAULT_VALUES.items():
        observed = values.get(name)
        if observed is None or observed.value.lower() != expected:
            raise ValueError(f"{name} must equal {expected}")
    for name in REQUIRED_VAULT_REFERENCES:
        observed = values.get(name)
        if observed is None:
            raise ValueError(f"{name} is missing")
        if not observed.value.startswith("vault://"):
            raise ValueError(f"{name} must be a Vault reference")

    infrastructure_secrets = (
        "MSSQL_SA_PASSWORD",
        "GRAPH_PASSWORD",
        "MONGO_ROOT_PASSWORD",
        "MONGO_REPLICA_SET_KEY",
        "TEMPORAL_DB_PASSWORD",
        "VALKEY_PASSWORD",
    )
    for name in infrastructure_secrets:
        parsed_value = values.get(name)
        if parsed_value is None:
            raise ValueError(f"{name} is missing")
        if not template_mode:
            _validate_non_placeholder(name, parsed_value.value)

    print(f"Validated {len(values)} dotenv assignments without displaying values.")
    return len(values)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--simulation", action="store_true")
    parser.add_argument("--template-mode", action="store_true")
    args = parser.parse_args()
    if args.path is not None and args.env_file is not None:
        parser.error("provide either path or --env-file, not both")
    path = args.env_file or args.path
    if path is None:
        parser.error("a dotenv path is required")
    try:
        validate(path, simulation=args.simulation, template_mode=args.template_mode)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Append missing non-secret runtime settings to an existing repository .env file.

The script never reads, prints, replaces, or derives credential values. It only
adds version-controlled Vault references and safe sentinel values required by
current Linux runtime code.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
from pathlib import Path

ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=")

SAFE_DEFAULTS: tuple[tuple[str, str], ...] = (
    ("VAULT_VERSION", "2.0.3"),
    ("PLATFORM_VAULT_ENABLED", "true"),
    ("PLATFORM_VAULT_ADDRESS", "http://127.0.0.1:8200"),
    ("PLATFORM_VAULT_TOKEN_FILE", ".vault-local/return-platform.token"),
    ("PLATFORM_VAULT_VERIFY_TLS", "false"),
    ("PLATFORM_VAULT_TIMEOUT_SECONDS", "5"),
    (
        "PLATFORM_MONGO_DSN_SECRET_REFERENCE",
        "'vault://secret/production/data-sources/mongodb#dsn'",
    ),
    (
        "PLATFORM_SOURCE_MONGO_DSN_SECRET_REFERENCE",
        "'vault://secret/production/data-sources/mongodb#source_dsn'",
    ),
    (
        "PLATFORM_NEO4J_PASSWORD_SECRET_REFERENCE",
        "'vault://secret/production/data-sources/neo4j#password'",
    ),
    (
        "PLATFORM_VALKEY_PASSWORD_SECRET_REFERENCE",
        "'vault://secret/production/data-sources/valkey#password'",
    ),
    (
        "PLATFORM_SQLSERVER_PASSWORD_SECRET_REFERENCE",
        "'vault://secret/production/data-sources/sqlserver#password'",
    ),
    (
        "PLATFORM_VALIDATION_FINGERPRINT_KEY_SECRET_REFERENCE",
        "'vault://secret/production/platform/validation#fingerprint_key'",
    ),
    ("PLATFORM_VALIDATION_FINGERPRINT_KEY", "vault-resolved"),
    (
        "PLATFORM_CONTACT_LOOKUP_HMAC_KEY_SECRET_REFERENCE",
        "'vault://secret/production/platform/contact-lookup#hmac_key'",
    ),
    ("PLATFORM_CONTACT_LOOKUP_HMAC_KEY", "vault-resolved"),
    (
        "PLATFORM_AI_ALLOWED_ENDPOINT_HOSTS",
        "'[\"generativelanguage.googleapis.com\",\"integrate.api.nvidia.com\",\"api.openai.com\",\"api.anthropic.com\"]'",
    ),
    (
        "PLATFORM_DATA_SOURCE_ALLOWED_HOSTS",
        "'[\"mongodb\",\"source-mongodb\",\"sqlserver\",\"neo4j\",\"localhost\",\"127.0.0.1\",\"::1\"]'",
    ),
    ("PLATFORM_GOOGLE_API_KEY_REFERENCES", "'[]'"),
    ("PLATFORM_NVIDIA_API_KEY_REFERENCES", "'[]'"),
    ("PLATFORM_OPENAI_API_KEY_REFERENCES", "'[]'"),
    ("PLATFORM_ANTHROPIC_API_KEY_REFERENCES", "'[]'"),
)

MIGRATABLE_JSON_LISTS = {
    name: value
    for name, value in SAFE_DEFAULTS
    if name in {"PLATFORM_AI_ALLOWED_ENDPOINT_HOSTS", "PLATFORM_DATA_SOURCE_ALLOWED_HOSTS"}
}


def existing_names(path: Path) -> set[str]:
    names: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        match = ASSIGNMENT.match(raw_line.strip())
        if match is not None:
            names.add(match.group(1))
    return names


def update(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        raise FileNotFoundError(path)
    present = existing_names(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    migrated: set[str] = set()
    normalized_lines: list[str] = []
    for line in lines:
        match = ASSIGNMENT.match(line.strip())
        name = match.group(1) if match is not None else None
        replacement = MIGRATABLE_JSON_LISTS.get(name or "")
        if replacement is not None:
            raw = line.split("=", 1)[1].strip()
            value = raw[1:-1] if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"" else raw
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                decoded = None
            if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
                normalized_lines.append(f"{name}={replacement}")
                migrated.add(name or "")
                continue
        normalized_lines.append(line)
    if migrated:
        path.write_text("\n".join(normalized_lines) + "\n", encoding="utf-8")
    missing = tuple((name, value) for name, value in SAFE_DEFAULTS if name not in present)
    if missing:
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write("\n# Vault-backed runtime references added by Linux bootstrap.\n")
            for name, value in missing:
                stream.write(f"{name}={value}\n")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return tuple(sorted({name for name, _ in missing} | migrated))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    added = update(args.env_file.resolve())
    print(f"runtime_env_keys_added={len(added)}")
    print(f"runtime_env_permissions={oct(stat.S_IMODE(args.env_file.resolve().stat().st_mode))}")
    return os.EX_OK


if __name__ == "__main__":
    raise SystemExit(main())

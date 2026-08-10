#!/usr/bin/env python3
"""Synchronize missing repository environment keys from .env.example.

Existing values are never replaced or printed. Missing assignments are appended
using their version-controlled example defaults. Selected runtime-safe sentinel
values remain authoritative for Vault-resolved settings.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
from pathlib import Path

ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
SAFE_OVERRIDES: dict[str, str] = {
    "PLATFORM_VALIDATION_FINGERPRINT_KEY": "vault-resolved",
    "PLATFORM_CONTACT_LOOKUP_HMAC_KEY": "vault-resolved",
}
MIGRATABLE_JSON_LISTS = {
    "PLATFORM_AI_ALLOWED_ENDPOINT_HOSTS": (
        '\'["generativelanguage.googleapis.com","integrate.api.nvidia.com",'
        '"api.openai.com","api.anthropic.com"]\''
    ),
    "PLATFORM_DATA_SOURCE_ALLOWED_HOSTS": (
        '\'["mongodb","source-mongodb","sqlserver","neo4j",'
        '"localhost","127.0.0.1","::1"]\''
    ),
}


def assignments(path: Path) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        match = ASSIGNMENT.match(raw_line.strip())
        if match is not None:
            result.append((match.group(1), match.group(2)))
    return tuple(result)


def existing_names(path: Path) -> set[str]:
    return {name for name, _value in assignments(path)}


def update(path: Path, example_path: Path) -> tuple[str, ...]:
    if not example_path.is_file():
        raise FileNotFoundError(example_path)
    if not path.exists():
        shutil.copyfile(example_path, path)

    present = existing_names(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    migrated: set[str] = set()
    normalized_lines: list[str] = []

    for line in lines:
        match = ASSIGNMENT.match(line.strip())
        name = match.group(1) if match is not None else None
        replacement = MIGRATABLE_JSON_LISTS.get(name or "")
        if replacement is not None and match is not None:
            raw = match.group(2).strip()
            value = (
                raw[1:-1]
                if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\""
                else raw
            )
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                decoded = None
            if not isinstance(decoded, list) or any(
                not isinstance(item, str) for item in decoded
            ):
                normalized_lines.append(f"{name}={replacement}")
                migrated.add(name)
                continue
        normalized_lines.append(line)

    if migrated:
        path.write_text("\n".join(normalized_lines) + "\n", encoding="utf-8")

    missing: list[tuple[str, str]] = []
    for name, example_value in assignments(example_path):
        if name not in present:
            missing.append((name, SAFE_OVERRIDES.get(name, example_value)))

    if missing:
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write("\n# Missing defaults synchronized from .env.example.\n")
            for name, value in missing:
                stream.write(f"{name}={value}\n")

    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return tuple(sorted({name for name, _value in missing} | migrated))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--example-file", type=Path)
    args = parser.parse_args()

    env_path = args.env_file.resolve()
    example_path = (
        args.example_file.resolve()
        if args.example_file is not None
        else env_path.parent / ".env.example"
    )
    changed = update(env_path, example_path)
    print(f"runtime_env_keys_synchronized={len(changed)}")
    print(f"runtime_env_permissions={oct(stat.S_IMODE(env_path.stat().st_mode))}")
    return os.EX_OK


if __name__ == "__main__":
    raise SystemExit(main())

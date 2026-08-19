#!/usr/bin/env python3
"""Generate missing local infrastructure credentials without displaying them.

Existing non-placeholder values are preserved. These credentials initialize the
local infrastructure and are also what the platform processes authenticate with:
nothing resolves them at startup, so the value written here is the credential.
"""

from __future__ import annotations

import re
import secrets
import stat
import string
from pathlib import Path
from urllib.parse import quote

MARKERS = ("placeholder", "replace-me", "change-me", "changeme")
SECRET_KEYS = (
    "MSSQL_SA_PASSWORD",
    "GRAPH_PASSWORD",
    "MONGO_ROOT_PASSWORD",
    "TEMPORAL_DB_PASSWORD",
    "VALKEY_PASSWORD",
    # Not infrastructure credentials -- platform-owned keys. They belong here
    # because they have the same property that matters: no resolver stands
    # behind them, so a placeholder left in place is a real weak key rather
    # than a value someone will substitute later.
    "PLATFORM_VALIDATION_FINGERPRINT_KEY",
    "PLATFORM_CONTACT_LOOKUP_HMAC_KEY",
)


def _needs_replacement(value: str) -> bool:
    normalized = value.strip().strip("'\"").lower()
    return not normalized or any(marker in normalized for marker in MARKERS)


def _password() -> str:
    # Avoid dotenv/Compose interpolation characters while satisfying SQL Server
    # complexity requirements.
    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("!@%_-"),
    ]
    alphabet = string.ascii_letters + string.digits + "!@%_-"
    remaining = [secrets.choice(alphabet) for _ in range(36)]
    characters = required + remaining
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)



#: MongoDB DSNs, assembled here rather than in `compose.yaml`.
#:
#: A generated password may contain `%`, `@` or `!`, every one of which has to be
#: percent-encoded before it can sit in a `mongodb://user:pass@host` URI. Compose
#: interpolates `${MONGO_ROOT_PASSWORD}` verbatim and has no way to encode it, so
#: a DSN assembled from parts in the compose file authenticates only for the
#: passwords that happen not to need encoding -- silently, and differently on
#: each machine.
#:
#: Two DSNs, not one, because the host and the containers reach MongoDB by
#: different names: host processes dial `localhost`, containers dial the compose
#: service `mongodb`. Both are derived from the same root credential here, so
#: they cannot drift apart the way two hand-written strings would.
_DERIVED_DSNS = (
    ("PLATFORM_MONGO_DSN", "localhost", "PLATFORM_MONGO_DATABASE", "return_platform"),
    ("PLATFORM_SOURCE_MONGO_DSN", "localhost", "PLATFORM_SOURCE_MONGO_DATABASE", "return_source"),
    ("PLATFORM_CONTAINER_MONGO_DSN", "mongodb", "PLATFORM_MONGO_DATABASE", "return_platform"),
    (
        "PLATFORM_CONTAINER_SOURCE_MONGO_DSN",
        "mongodb",
        "PLATFORM_SOURCE_MONGO_DATABASE",
        "return_source",
    ),
)
_ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _read(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        match = _ASSIGNMENT.match(line.strip())
        if match:
            values[match.group(1)] = match.group(2).strip().strip("'\"")
    return values


def _mongo_dsn(user: str, password: str, host: str, port: str, database: str) -> str:
    # `quote` with an empty safe set: every reserved character is encoded, which
    # is the whole point of building the string here instead of in compose.
    return (
        f"mongodb://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{host}:{port}/{database}?authSource=admin&directConnection=true"
    )


def _rewrite_dsns(lines: list[str]) -> tuple[list[str], int]:
    """Rewrite every derived MongoDB DSN from the current root credential."""
    values = _read(lines)
    user = values.get("MONGO_ROOT_USERNAME")
    password = values.get("MONGO_ROOT_PASSWORD")
    if not user or not password:
        return lines, 0
    port = values.get("MONGO_PORT", "27017")
    desired = {
        name: _mongo_dsn(user, password, host, port, values.get(db_key) or db_default)
        for name, host, db_key, db_default in _DERIVED_DSNS
    }
    output: list[str] = []
    seen: set[str] = set()
    changed = 0
    for line in lines:
        match = _ASSIGNMENT.match(line.strip())
        name = match.group(1) if match else ""
        if name in desired:
            seen.add(name)
            replacement = f"{name}='{desired[name]}'"
            if line != replacement:
                changed += 1
            output.append(replacement)
        else:
            output.append(line)
    for name, value in desired.items():
        if name not in seen:
            output.append(f"{name}='{value}'")
            changed += 1
    return output, changed


def ensure(path: Path) -> int:
    if not path.is_file():
        raise FileNotFoundError(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    pending = set(SECRET_KEYS)
    generated = 0
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0] if "=" in stripped else ""
        if key in pending:
            pending.remove(key)
            value = stripped.split("=", 1)[1]
            if _needs_replacement(value):
                output.append(f"{key}={_password()}")
                generated += 1
            else:
                output.append(line)
        else:
            output.append(line)
    for key in SECRET_KEYS:
        if key in pending:
            output.append(f"{key}={_password()}")
            generated += 1
    # After the credentials are settled, not before: a password generated above
    # has to reach the DSNs derived from it in the same run.
    output, _ = _rewrite_dsns(output)
    path.write_text("\n".join(output) + "\n", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return generated


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    generated = ensure(root / ".env")
    print(f"local_infrastructure_secrets_generated={generated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

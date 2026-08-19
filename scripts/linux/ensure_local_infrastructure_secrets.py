#!/usr/bin/env python3
"""Generate missing local infrastructure credentials without displaying them.

Existing non-placeholder values are preserved. These credentials initialize the
local infrastructure and are also what the platform processes authenticate with:
nothing resolves them at startup, so the value written here is the credential.
"""

from __future__ import annotations

import secrets
import stat
import string
from pathlib import Path

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

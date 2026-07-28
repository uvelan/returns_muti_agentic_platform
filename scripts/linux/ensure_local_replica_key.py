#!/usr/bin/env python3
"""Ensure the local uncommitted dotenv file has a strong MongoDB replica-set key."""

from __future__ import annotations

import secrets
import stat
from pathlib import Path

MARKERS = ("placeholder", "replace-me", "change-me", "changeme")
KEY = "MONGO_REPLICA_SET_KEY"


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    path = root / ".env"
    if not path.is_file():
        raise SystemExit(".env is missing; run scripts/bootstrap_host.sh first")
    lines = path.read_text(encoding="utf-8").splitlines()
    generated = secrets.token_hex(48)
    replaced = False
    found = False
    output: list[str] = []
    for line in lines:
        if line.startswith(f"{KEY}="):
            found = True
            value = line.split("=", 1)[1].strip().strip("'\"")
            if not value or any(marker in value.lower() for marker in MARKERS):
                output.append(f"{KEY}={generated}")
                replaced = True
            else:
                output.append(line)
        else:
            output.append(line)
    if not found:
        output.append(f"{KEY}={generated}")
        replaced = True
    if replaced:
        path.write_text("\n".join(output) + "\n", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    print("MongoDB replica-set key is configured without displaying its value.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

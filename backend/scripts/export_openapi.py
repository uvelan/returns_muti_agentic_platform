#!/usr/bin/env python3
"""The single OpenAPI generator. Every committed snapshot comes from here.

**Why the settings are pinned.** This script used to call `create_app()` with
ambient settings while `scripts/check_openapi_drift.py` called it with explicit
test settings. Same serialization, different app -- feature flags and optional
integrations change which routers mount -- so the two produced different
documents from the same commit. That is how the repository ended up with
`frontend/openapi/return-platform.openapi.json` at 631 KB and
`openapi/return-platform.openapi.json` at 512 KB, both "current".

A contract whose content depends on the exporter's environment is not a
contract. `contract_settings()` is therefore explicit and env-independent, and
both the exporter and the drift check use it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = BACKEND_ROOT / "src"
REPOSITORY_ROOT = BACKEND_ROOT.parent
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from return_platform.configuration.settings import Settings  # noqa: E402
from return_platform.main import create_app  # noqa: E402


def contract_settings() -> Settings:
    """The one settings basis every committed contract is generated from.

    `model_construct` skips validation deliberately: this is not a runtime
    configuration, it is a fixed input chosen so the emitted document depends
    only on the code. Reading real settings here would make the contract vary
    with whoever ran the export.
    """
    return Settings.model_construct(
        environment="test",
        mongo_dsn="mongodb://localhost:27017",
        sqlserver_host="localhost",
        sqlserver_password="test",
        neo4j_password="test",
        frontend_cors_origin="http://localhost:3000",
    )


def build_openapi_document() -> dict[str, Any]:
    document: dict[str, Any] = create_app(custom_settings=contract_settings()).openapi()
    return document


def build_openapi_bytes() -> bytes:
    """Canonical serialization: sorted keys, two-space indent, trailing newline.

    Byte-level determinism matters because the drift check compares bytes, not
    parsed documents -- a formatting difference would otherwise read as a
    contract change.
    """
    return json.dumps(build_openapi_document(), indent=2, sort_keys=True).encode("utf-8") + b"\n"


def main() -> int:
    output = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) > 1
        else REPOSITORY_ROOT / "frontend" / "openapi" / "return-platform.openapi.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(build_openapi_bytes())
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

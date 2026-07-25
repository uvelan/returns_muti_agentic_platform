#!/usr/bin/env python3
"""Run dependency-light Stage 4 contract tests without loading pytest conftest."""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = ROOT / "backend" / "src"
TEST_MODULE = ROOT / "backend" / "tests" / "test_stage4_schema_and_seed_contracts.py"
OUTPUT = (
    ROOT
    / "docs"
    / "evidence"
    / "stage4_hld_alignment"
    / "source_contract_validation.json"
)


def _load_test_module() -> ModuleType:
    sys.path.insert(0, str(BACKEND_SRC))
    spec = importlib.util.spec_from_file_location(
        "stage4_source_contracts", TEST_MODULE
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load contract test module: {TEST_MODULE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(name: str, function: Callable[[], None]) -> dict[str, Any]:
    try:
        function()
    except Exception as error:  # pragma: no cover - evidence path
        return {
            "name": name,
            "status": "FAIL",
            "errorType": type(error).__name__,
            "safeMessage": str(error),
            "traceback": traceback.format_exc(),
        }
    return {"name": name, "status": "PASS"}


def main() -> int:
    module = _load_test_module()
    names = sorted(name for name in vars(module) if name.startswith("test_"))
    results = [_run(name, getattr(module, name)) for name in names]
    failures = [result for result in results if result["status"] != "PASS"]
    payload = {
        "stage": "Stage 4 — HLD-Aligned Source Contracts",
        "validationLevel": "SOURCE_VALIDATED",
        "command": "python3.13 scripts/validate_stage4_contracts.py",
        "generatedAt": datetime.now(UTC).isoformat(),
        "pythonVersion": sys.version.split()[0],
        "testsRun": len(results),
        "exitCode": int(bool(failures)),
        "status": "FAILED" if failures else "PASSED",
        "results": results,
        "limitations": [
            "This gate intentionally bypasses pytest conftest so schema and seed "
            "contracts can run without Neo4j, MongoDB, SQL Server, or Temporal "
            "clients installed.",
            "It does not replace the dependency-backed backend unit and integration suite.",
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())

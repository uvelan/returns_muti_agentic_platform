#!/usr/bin/env python3
"""Export OpenAPI from a host Python environment without Docker."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = BACKEND_ROOT / "src"
REPOSITORY_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))

from return_platform.main import create_app  # noqa: E402


def main() -> int:
    output = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) > 1
        else REPOSITORY_ROOT / "frontend" / "openapi" / "return-platform.openapi.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Architecture test: platform/* names no type owned by any domain module.

Verified by a static AST import scan, not by convention (design doc section 13.1,
rule R2a).
"""

from __future__ import annotations

import ast
from pathlib import Path

PLATFORM_DIR = Path(__file__).resolve().parents[2] / "src" / "return_platform" / "platform"

FORBIDDEN_PREFIXES = (
    "return_platform.configuration",
    "return_platform.graph",
    "return_platform.agents",
    "return_platform.business",
    "return_platform.ai",
    "return_platform.graph_schema_analyzer",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def test_platform_imports_no_domain_module() -> None:
    violations: list[tuple[Path, str]] = []
    for path in sorted(PLATFORM_DIR.rglob("*.py")):
        for imported in _imported_modules(path):
            if imported.startswith(FORBIDDEN_PREFIXES):
                violations.append((path, imported))
    assert not violations, (
        f"platform/* must not import a domain module (design doc section 13.1); found: {violations}"
    )

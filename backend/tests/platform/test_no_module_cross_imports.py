"""Architecture test: no stray adapters/ package, and no migrated module imports
another migrated module directly.

`configuration/` and `agents/` are existing pre-consolidation directory names that the
target architecture reuses -- a directory-name check alone cannot tell "legacy code
not yet migrated" from "consolidated module." A package counts as migrated once it
exposes a ModuleFactory in module.py (design doc section 2.1, rule R3), so this test is
a no-op today and starts enforcing itself automatically as each module lands its
module.py, with no future edit required here.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "return_platform"

NEW_STYLE_MODULES = (
    "configuration",
    "graph",
    "agents",
    "business",
    "ai",
    "graph_schema_analyzer",
)


def _is_migrated(module_dir: Path) -> bool:
    return (module_dir / "module.py").is_file()


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def test_no_adapters_package_outside_bootstrap() -> None:
    scan_roots = [SRC / "platform"] + [
        SRC / name for name in NEW_STYLE_MODULES if _is_migrated(SRC / name)
    ]
    stray = [path for base in scan_roots for path in base.rglob("adapters") if path.is_dir()]
    assert not stray, (
        "only bootstrap/adapters/ may bind cross-module dependencies; found stray "
        f"adapters/ packages: {stray}"
    )


def test_new_style_modules_do_not_import_each_other() -> None:
    migrated = [name for name in NEW_STYLE_MODULES if _is_migrated(SRC / name)]
    violations: list[tuple[Path, str]] = []
    for module_name in migrated:
        forbidden = tuple(f"return_platform.{other}" for other in migrated if other != module_name)
        if not forbidden:
            continue
        for path in sorted((SRC / module_name).rglob("*.py")):
            for imported in _imported_modules(path):
                if imported.startswith(forbidden):
                    violations.append((path, imported))
    assert not violations, (
        "migrated modules communicate through platform.capabilities, never direct "
        f"imports; found: {violations}"
    )

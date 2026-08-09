"""Architecture test: the Graph Schema Analyzer depends on no other business module.

The module's whole design premise (design doc section 2.7) is that it reaches
everything outside itself through `ports/`, bound in `bootstrap/adapters/`. That
is only true if it is actually checked -- a single convenience import of
`ai.gateway` or `dynamic_knowledge.schema` would quietly undo it, and would look
perfectly reasonable in review.

`tests/platform/test_no_module_cross_imports.py` covers the *target*-architecture
module names once each lands a `module.py`. This test additionally forbids the
pre-consolidation packages that still exist today (`dynamic_knowledge`,
`data_platform`, `v2`, ...), which that test cannot know about.
"""

from __future__ import annotations

import ast
from pathlib import Path

ANALYZER_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "return_platform" / "graph_schema_analyzer"
)

# Every top-level package under return_platform that is business code rather than
# shared platform infrastructure. `platform`, `security`, and `shared` are
# deliberately absent: those are cross-cutting infrastructure the analyzer is
# permitted to use directly (see ports/system_store_port.py's own note).
FORBIDDEN_PREFIXES = (
    "return_platform.agents",
    "return_platform.ai",
    "return_platform.ai_gateway",
    "return_platform.api",
    "return_platform.business",
    "return_platform.canonical",
    "return_platform.configuration",
    "return_platform.conversation",
    "return_platform.data_console",
    "return_platform.data_governance",
    "return_platform.data_platform",
    "return_platform.dependency_simulation",
    "return_platform.dynamic_knowledge",
    "return_platform.graph",
    "return_platform.operations",
    "return_platform.source_connectors",
    "return_platform.v2",
    "return_platform.validation",
    "return_platform.workflows",
    "return_platform.workers",
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


def _is_forbidden(imported: str, forbidden: tuple[str, ...]) -> bool:
    """Match on package boundaries, not raw string prefixes.

    A bare `startswith` is wrong here and not subtly so: `return_platform.graph`
    prefix-matches `return_platform.graph_schema_analyzer`, so every one of this
    module's own internal imports would be reported as a violation of itself.
    Requiring the following character to be a `.` is what makes the check mean
    "this package or something inside it".
    """
    return any(imported == name or imported.startswith(f"{name}.") for name in forbidden)


def test_analyzer_imports_no_other_business_module() -> None:
    violations: list[tuple[str, str]] = []
    for path in sorted(ANALYZER_DIR.rglob("*.py")):
        for imported in _imported_modules(path):
            if _is_forbidden(imported, FORBIDDEN_PREFIXES):
                violations.append((path.name, imported))
    assert not violations, (
        "graph_schema_analyzer must reach other modules only through ports/ bound in "
        f"bootstrap/adapters/ (design doc 2.7); found direct imports: {violations}"
    )


def test_analyzer_has_no_adapters_package() -> None:
    """Binding a port to a concrete other-module implementation belongs in
    bootstrap/adapters/, which is the only place permitted to see both sides."""
    stray = [path for path in ANALYZER_DIR.rglob("adapters") if path.is_dir()]
    assert not stray, f"analyzer must own no adapters/ package; found: {stray}"


def test_domain_layer_performs_no_io() -> None:
    """The domain package must stay pure: no port, no persistence, no framework.

    Checked because the invariants living there (session transitions, sample
    classification, content addressing) are only trustworthy if they cannot be
    bypassed by something reaching out mid-validation.
    """
    domain_dir = ANALYZER_DIR / "domain"
    disallowed = (
        "return_platform.graph_schema_analyzer.ports",
        "return_platform.graph_schema_analyzer.persistence",
        "return_platform.graph_schema_analyzer.api",
        "return_platform.platform",
        "fastapi",
        "pymongo",
        "motor",
    )
    violations: list[tuple[str, str]] = []
    for path in sorted(domain_dir.rglob("*.py")):
        for imported in _imported_modules(path):
            if _is_forbidden(imported, disallowed):
                violations.append((path.name, imported))
    assert not violations, f"analyzer domain/ must stay pure; found: {violations}"

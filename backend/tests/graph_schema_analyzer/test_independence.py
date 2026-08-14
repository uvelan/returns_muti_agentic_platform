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
    # `return_platform.ai_gateway` was listed here as well while the deprecated
    # re-export shim existed. `_is_forbidden` matches on package boundaries, so
    # `return_platform.ai` never covered it -- but the package is now deleted, and
    # `tests/platform/test_ai_lane_boundary.py` forbids that path repository-wide.
    "return_platform.api",
    "return_platform.business",
    "return_platform.canonical",
    "return_platform.configuration",
    "return_platform.conversation",
    "return_platform.data_governance",
    "return_platform.data_platform",
    "return_platform.dependency_simulation",
    "return_platform.dynamic_knowledge",
    "return_platform.graph",
    "return_platform.operations",
    "return_platform.source_connectors",
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


# --- ANZ-01: composable by an application that is not this one ---------------
#
# The test above permits `platform` and `security` as "cross-cutting
# infrastructure the analyzer is permitted to use directly", which is the right
# rule for a module living inside this host and the wrong one for the question
# both audits actually asked: could a *second* application compose this?
#
# A second application has no `return_platform.platform` and no
# `return_platform.security`. So the rings below the composition root have to
# reach the host only through ports. `api/`, `persistence/` and `module.py` are
# exempt by design -- they are this host's composition root, and a second
# application writes its own. `composition.py` is exempt because it is the seam:
# it is where the default bindings live, and having exactly one such place is
# the point.

#: Rings that must be composable without this host.
PORTABLE_RINGS = ("domain", "reasoning", "ports", "application")

HOST_PREFIXES = ("return_platform.platform", "return_platform.security")

#: The one remaining host coupling below the composition root, named rather than
#: silently permitted.
#:
#: `draft_service` drives `ProposalKernel` -- this platform's approval lifecycle
#: -- and governance is deliberately not among the interfaces either audit
#: enumerated for portability (source connectors, graph target, AI gateway,
#: scope policy, masking policy, configuration, persistence). It is also on the
#: program's explicit preserve list, so wrapping it in a port is a change to
#: make on purpose rather than as a side effect of a packaging task.
#:
#: Listed as a single file, not a prefix: a *second* module reaching for
#: governance would fail this test, which is the behaviour worth having.
KNOWN_HOST_COUPLINGS = frozenset({"application/draft_service.py"})


def test_the_portable_rings_reach_the_host_only_through_ports() -> None:
    """The finding both audits scored: the analyzer core is independent, but its
    packaging was not -- a host could supply sources, a graph target, an AI
    gateway and persistence, and still had no way to supply its own masking or
    its own retention scope, because the application layer constructed this
    platform's implementations directly."""
    violations: list[tuple[str, str]] = []
    for ring in PORTABLE_RINGS:
        for path in sorted((ANALYZER_DIR / ring).rglob("*.py")):
            relative = f"{ring}/{path.name}"
            if relative in KNOWN_HOST_COUPLINGS:
                continue
            for imported in _imported_modules(path):
                if _is_forbidden(imported, HOST_PREFIXES):
                    violations.append((relative, imported))
    assert not violations, (
        "these rings must be composable by an application that does not have this "
        f"host's platform or security packages; found: {violations}. If a new host "
        "dependency is genuinely needed, declare it as a Protocol in ports/ and bind "
        "the default in composition.py."
    )


def test_every_known_host_coupling_is_still_real() -> None:
    """An exemption that stops being needed has to stop existing.

    Without this, `KNOWN_HOST_COUPLINGS` is a place a future coupling can hide:
    a file listed there is unchecked forever, including after someone removes
    the very import it was listed for.
    """
    stale = [
        relative
        for relative in sorted(KNOWN_HOST_COUPLINGS)
        if not any(
            _is_forbidden(imported, HOST_PREFIXES)
            for imported in _imported_modules(ANALYZER_DIR / relative)
        )
    ]
    assert not stale, f"these no longer couple to the host; drop the exemption: {stale}"


def test_the_composition_seam_is_the_only_default_host_binding() -> None:
    """Defaults are allowed to exist -- composing inside this platform should stay
    a one-liner -- but only in one place. Two seams would mean a host that
    replaced one still inherited the other without being told."""
    seam = ANALYZER_DIR / "composition.py"
    assert seam.exists(), "the composition contract ANZ-01 asks for is missing"
    assert "redaction" in seam.read_text(encoding="utf-8"), (
        "composition.py must be where the masking and scope defaults are bound"
    )

    leaked = [
        f"{ring}/{path.name}"
        for ring in PORTABLE_RINGS
        for path in sorted((ANALYZER_DIR / ring).rglob("*.py"))
        if "platform.redaction" in path.read_text(encoding="utf-8")
        and f"{ring}/{path.name}" not in {"ports/masking_port.py"}
    ]
    assert not leaked, f"a default host binding leaked outside composition.py: {leaked}"

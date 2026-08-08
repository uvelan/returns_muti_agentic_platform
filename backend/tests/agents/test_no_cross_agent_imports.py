"""Architecture test: no agent imports another agent module.

Phase 5's contract (agents/README.md): "This agent does not directly invoke another
agent." Sequencing across agents is the orchestrator's job, never an agent's.

This does not forbid an agent importing a *type* from another module for a method
parameter annotation -- OrderAnalysisAgent imports AIGatewayService because its
caller passes one in explicitly (see order_analysis.md); it never reaches for one
itself. What it forbids is one agent module importing another agent module.
"""

from __future__ import annotations

import ast
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[2] / "src" / "return_platform" / "agents"

AGENT_MODULES = (
    "order_discovery",
    "order_analysis",
    "return_workflow",
    "fulfillment",
    "bay_assignment",
    "feedback",
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


def test_no_agent_imports_another_agent_module() -> None:
    violations: list[tuple[str, str]] = []
    for module_name in AGENT_MODULES:
        path = AGENTS_DIR / f"{module_name}.py"
        forbidden = tuple(
            f"return_platform.agents.{other}" for other in AGENT_MODULES if other != module_name
        )
        for imported in _imported_modules(path):
            if imported.startswith(forbidden):
                violations.append((module_name, imported))
    assert not violations, f"an agent imported another agent directly: {violations}"

"""Architecture test: no LangGraph/LangChain type leaks into the agents' public
contract surface.

Phase 5's AgentExecutionContext is deliberately platform-neutral (no `.ai`,
no `.knowledge`, no other agent, no domain type) precisely so LangGraph could land
underneath it later without a second refactor -- this test keeps that promise: nothing
in `agents/contracts/` (the shared plugin contract every agent implements) may import
`langgraph` or `langchain_core`.
"""

from __future__ import annotations

import ast
from pathlib import Path

CONTRACTS_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "return_platform" / "agents" / "contracts"
)

FORBIDDEN_PREFIXES = ("langgraph", "langchain_core", "langchain")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def test_agent_contracts_do_not_import_langgraph_or_langchain() -> None:
    violations: list[tuple[str, str]] = []
    for path in sorted(CONTRACTS_DIR.glob("*.py")):
        for imported in _imported_modules(path):
            if imported.startswith(FORBIDDEN_PREFIXES):
                violations.append((path.name, imported))
    assert not violations, (
        f"agents/contracts/ imported a LangGraph/LangChain type directly: {violations}"
    )

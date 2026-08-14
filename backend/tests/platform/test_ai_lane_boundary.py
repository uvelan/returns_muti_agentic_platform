"""Phase 13's two structural rules, enforced rather than described.

**"No provider/model literals in agents."** The point of naming an AI *task* --
`ORDER_AGENT_REASONING_V1`, not Gemini -- is that provider, model, credential and
tier become configuration resolved at dispatch time from health and quota state.
That is what makes failover, key rotation and tier escalation possible without
touching agent code. One hardcoded `"GOOGLE"` on a reasoning path quietly removes
all of it for that call path, and nothing else would notice.

**Scoped to the agent lane, deliberately.** An earlier draft of this test banned
provider strings everywhere outside `ai/` and found 37 hits -- every one of them
a validator's allowed-value set, a capability map built from settings, or an API
view model reporting which provider *did* serve a request. Recording the provider
that answered is the opposite of choosing one: it is the observability the
routing layer exists to produce, and banning it would delete audit data to
satisfy a rule about dispatch. So the check covers the packages that *invoke*
reasoning, where a literal really does mean a caller bypassed task-based
routing.

**One canonical AI package.** `ai_gateway/` was a deprecated re-export shim,
kept while ~30 modules outside the AI lane still imported it. Those imports were
rewritten and the shim deleted, so the rule is now the strong one: *nothing*
imports `return_platform.ai_gateway`. Checking only that `ai/` does not import
it -- which is what this file used to assert -- would pass trivially now that
the package does not exist, and would not notice a re-export layer being
reintroduced somewhere else.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src" / "return_platform"

#: Provider identifiers. A literal of one of these outside the AI lane means a
#: caller has chosen a provider instead of naming a task.
_PROVIDER_LITERALS = frozenset({"GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC", "OLLAMA", "SIMULATOR"})

#: Fragments that identify a concrete model. Substring-matched because model
#: names are versioned strings (`gemini-2.5-pro`, `gpt-4o-mini`) that no
#: exact-match list would keep up with.
_MODEL_FRAGMENTS = ("gemini-", "gpt-4", "gpt-5", "claude-3", "claude-4", "llama-", "nemotron")

#: The packages that invoke reasoning. A provider or model literal here means a
#: caller selected one instead of naming a task.
_AGENT_LANE = ("agents", "dynamic_knowledge", "workflows", "graph_schema_analyzer")


def _relative(path: Path) -> str:
    return path.relative_to(_SRC).as_posix()


def test_no_provider_or_model_literals_in_the_agent_lane() -> None:
    offenders: list[str] = []
    for package in _AGENT_LANE:
        for path in (_SRC / package).rglob("*.py"):
            relative = _relative(path)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                value = node.value
                if value in _PROVIDER_LITERALS:
                    offenders.append(f"{relative}:{node.lineno} provider literal {value!r}")
                elif any(fragment in value.lower() for fragment in _MODEL_FRAGMENTS):
                    offenders.append(f"{relative}:{node.lineno} model literal {value!r}")

    assert offenders == [], (
        "these name a provider or model directly instead of naming an AI task, which "
        "silently opts that call path out of failover, key rotation and tier "
        "escalation: " + "; ".join(offenders)
    )


def test_the_deprecated_shim_package_is_gone() -> None:
    """The re-export layer itself, not just the rule against importing it.

    A package that reappears is what makes an import sweep have to be done
    twice: every re-export is a second name for one object, and two names is
    how the AI lane ended up with three provider dispatch loops.
    """
    assert not (_SRC / "ai_gateway").exists(), (
        "return_platform/ai_gateway/ is back. It was a pure re-export of ai/; a second "
        "import path for the same objects is what the consolidation removed."
    )


def test_nothing_imports_the_deprecated_ai_gateway_path() -> None:
    """No module, test or script may import `return_platform.ai_gateway`.

    Scoped to the whole repository rather than to `ai/`. The narrow version --
    "the canonical package must not import its own shim" -- was the right rule
    while the shim existed and 30 callers depended on it, but with the package
    deleted it passes without looking at anything. This one keeps failing for a
    real reason: a caller that reaches for the old path is a caller about to
    recreate it.
    """
    backend = _SRC.parents[1]
    roots = (_SRC, backend / "tests", backend / "scripts", backend.parent / "scripts")
    offenders: list[str] = []
    inspected = 0
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            inspected += 1
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "return_platform.ai_gateway"
                ):
                    offenders.append(f"{path}:{node.lineno}")
                elif isinstance(node, ast.Import):
                    offenders.extend(
                        f"{path}:{node.lineno}"
                        for alias in node.names
                        if alias.name.startswith("return_platform.ai_gateway")
                    )

    # Self-verifying: if the roots stop resolving this would pass having read
    # nothing, which is exactly the failure mode it replaced.
    assert inspected > 500, (
        f"only {inspected} modules were inspected, so this check is no longer reading "
        "the source tree it is supposed to guard"
    )

    assert offenders == [], (
        "these import the deleted return_platform.ai_gateway re-export path; import the "
        "canonical module directly (`ai.routing.tasks`, `ai.routing.routes`, "
        "`ai.routing.selection`, `ai.gateway.models`, `ai.gateway.service`, `ai.safety`, "
        "`ai.providers`): " + ", ".join(offenders)
    )

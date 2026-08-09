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

**One canonical AI package.** `ai_gateway/` is now a deprecated re-export shim
(see its `__init__`), kept only because ~20 modules outside the AI lane still
import it and could not be moved in the same commit. New code must not deepen
that dependency, so the shim is allowed to exist while `ai/` is forbidden from
importing it -- a cycle back through the deprecated path would make the shim
permanent.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src" / "return_platform"
_AI = _SRC / "ai"

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


def test_the_canonical_ai_package_does_not_import_its_own_deprecated_shim() -> None:
    """`ai_gateway/` re-exports `ai/`. If `ai/` imported back through it the
    dependency would be circular in intent -- the shim could never be deleted,
    which is the entire point of Wave F's import sweep."""
    offenders: list[str] = []
    for path in _AI.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "return_platform.ai_gateway"
            ):
                offenders.append(f"{_relative(path)}:{node.lineno}")
            elif isinstance(node, ast.Import):
                offenders.extend(
                    f"{_relative(path)}:{node.lineno}"
                    for alias in node.names
                    if alias.name.startswith("return_platform.ai_gateway")
                )

    assert offenders == [], (
        "the canonical ai/ package imports the deprecated ai_gateway/ shim, which would "
        "make the shim impossible to delete: " + ", ".join(offenders)
    )

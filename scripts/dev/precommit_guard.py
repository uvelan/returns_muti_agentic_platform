"""Pre-commit blocker guard (Slice 3R, §4.3/§15 of the execution directive).

A gate, not a formatter or auto-fixer: exits non-zero with a concrete reason list on
any blocking issue. Does not auto-fix and does not install itself as a git hook
(installing hooks is a local-environment change the operator should make deliberately).

Usage:
    python scripts/dev/precommit_guard.py [--base <commit>]
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_BRANCH = "refactor/unified-return-platform"

_PRODUCTION_PLACEHOLDER_PATTERNS = [
    re.compile(r"\bTODO\b"),
    re.compile(r"\bFIXME\b"),
    re.compile(r"\bNotImplementedError\b"),
]
_LINT_SUPPRESSION_PATTERNS = [
    re.compile(
        r"#\s*noqa(?!:)"
    ),  # bare noqa (no code) hides everything, not just one rule
    re.compile(r"#\s*type:\s*ignore(?!\[)"),  # bare type: ignore hides everything
]
_SKIP_TEST_PATTERNS = [
    re.compile(r"@pytest\.mark\.skip"),
    re.compile(r"@pytest\.mark\.xfail"),
]
_SECRET_LOOKING_PATTERNS = [
    re.compile(
        r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*[\"'][A-Za-z0-9+/=_-]{16,}[\"']"
    ),
    re.compile(r"-----BEGIN (RSA |EC )?PRIVATE KEY-----"),
]


def _run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _git(args: list[str]) -> str:
    code, out = _run(["git", *args])
    if code != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out}")
    return out.strip()


def check_branch() -> list[str]:
    branch = _git(["branch", "--show-current"])
    if branch != EXPECTED_BRANCH:
        return [f"branch: expected '{EXPECTED_BRANCH}', currently on '{branch}'"]
    return []


def check_remote_divergence() -> list[str]:
    problems: list[str] = []
    code, _ = _run(["git", "fetch", "--prune", "origin"])
    if code != 0:
        problems.append(
            "remote: 'git fetch --prune origin' failed -- cannot verify divergence"
        )
        return problems
    try:
        local_head = _git(["rev-parse", "HEAD"])
        remote_head = _git(["rev-parse", f"origin/{EXPECTED_BRANCH}"])
    except RuntimeError as exc:
        problems.append(f"remote: {exc}")
        return problems
    if local_head != remote_head:
        ahead_behind = _git(
            ["rev-list", "--left-right", "--count", f"{local_head}...{remote_head}"]
        )
        problems.append(
            f"remote: local HEAD ({local_head[:8]}) differs from origin/{EXPECTED_BRANCH} "
            f"({remote_head[:8]}); ahead/behind: {ahead_behind}"
        )
    return problems


def check_diff_whitespace() -> list[str]:
    code, out = _run(["git", "diff", "--cached", "--check"])
    if code != 0:
        return [f"whitespace: 'git diff --cached --check' found issues:\n{out.strip()}"]
    return []


def staged_files() -> list[str]:
    out = _git(["diff", "--cached", "--name-only"])
    return [line for line in out.splitlines() if line.strip()]


def check_unrelated_staged_files(expected_prefixes: list[str] | None) -> list[str]:
    if not expected_prefixes:
        return []
    problems = []
    for path in staged_files():
        if not any(path.startswith(prefix) for prefix in expected_prefixes):
            problems.append(f"unrelated staged file (not under expected scope): {path}")
    return problems


def staged_diff_added_lines() -> dict[str, list[str]]:
    """Map of staged file -> list of newly added lines (the '+' side of the diff)."""
    code, out = _run(["git", "diff", "--cached", "-U0"])
    if code != 0:
        return {}
    added: dict[str, list[str]] = {}
    current_file = None
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[len("+++ b/") :]
            added.setdefault(current_file, [])
        elif line.startswith("+") and not line.startswith("+++") and current_file:
            added[current_file].append(line[1:])
    return added


def check_production_placeholders(added: dict[str, list[str]]) -> list[str]:
    problems = []
    for path, lines in added.items():
        if (
            "/tests/" in path
            or path.startswith("tests/")
            or path.endswith("_test.py")
            or "/test_" in path
        ):
            continue  # placeholders/TODOs in tests are not "deferred correctness" in production code
        for line in lines:
            for pattern in _PRODUCTION_PLACEHOLDER_PATTERNS:
                if pattern.search(line):
                    problems.append(
                        f"production placeholder in {path}: {line.strip()[:120]}"
                    )
    problems += check_new_stub_function_bodies(added)
    return problems


def check_new_stub_function_bodies(added: dict[str, list[str]]) -> list[str]:
    """A bare `pass` is only a placeholder when it is a *function/method's entire body*
    (an unimplemented stub) -- not when it appears inside real control flow, e.g. a
    deliberate `except SomethingExpected: pass` fallthrough with logic before and after
    it. AST-based, and only flags a stub whose `def`/`async def` line itself was newly
    added in this diff -- a pre-existing stub elsewhere in an otherwise-touched file is
    not this diff's problem to fix."""
    problems: list[str] = []
    for path, new_lines in added.items():
        if not path.endswith(".py") or "/tests/" in path or path.startswith("tests/"):
            continue
        full_path = REPO_ROOT / path
        try:
            source = full_path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError:
            continue
        new_line_texts = {line.strip() for line in new_lines}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if len(node.body) != 1 or not isinstance(node.body[0], ast.Pass):
                continue
            def_line = source.splitlines()[node.lineno - 1].strip()
            if def_line in new_line_texts:
                problems.append(
                    f"new stub function body (pass-only) in {path}: {node.name}()"
                )
    return problems


def check_lint_suppressions(added: dict[str, list[str]]) -> list[str]:
    problems = []
    for path, lines in added.items():
        for line in lines:
            for pattern in _LINT_SUPPRESSION_PATTERNS:
                if pattern.search(line):
                    problems.append(
                        f"new blanket lint/type suppression in {path}: {line.strip()[:120]}"
                    )
    return problems


def check_new_test_skips(added: dict[str, list[str]]) -> list[str]:
    problems = []
    for path, lines in added.items():
        for line in lines:
            for pattern in _SKIP_TEST_PATTERNS:
                if pattern.search(line):
                    problems.append(
                        f"new skipped/xfail test marker in {path}: {line.strip()[:120]}"
                    )
    return problems


def check_secret_looking_values(added: dict[str, list[str]]) -> list[str]:
    problems = []
    for path, lines in added.items():
        for line in lines:
            for pattern in _SECRET_LOOKING_PATTERNS:
                if pattern.search(line):
                    problems.append(
                        f"secret/key-looking value in {path}: {line.strip()[:60]}..."
                    )
    return problems


def check_physical_platform_literals(added: dict[str, list[str]]) -> list[str]:
    """New `platform_*` collection-name string literals outside the SystemStore
    implementation are a violation of "logical SystemStore names only"."""
    problems = []
    allowed_prefix = "backend/src/return_platform/platform/system_store/"
    pattern = re.compile(r'["\']platform_[a-z_]+["\']')
    for path, lines in added.items():
        if path.startswith(allowed_prefix) or "/tests/" in path:
            continue
        for line in lines:
            if pattern.search(line):
                problems.append(
                    f"physical 'platform_*' literal outside system_store in {path}: {line.strip()[:120]}"
                )
    return problems


def check_cross_module_imports() -> list[str]:
    """Reuse the existing architecture invariant test rather than re-implementing its
    logic -- run it directly and surface a failure as a guard finding."""
    backend = REPO_ROOT / "backend"
    if not backend.exists():
        return []
    proc = subprocess.run(
        [
            "poetry",
            "run",
            "pytest",
            "tests/platform/test_no_module_cross_imports.py",
            "-q",
        ],
        cwd=str(backend),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        return [
            f"cross-module import violation detected:\n{(proc.stdout + proc.stderr)[-2000:]}"
        ]
    return []


def check_execution_ledger_current(current_slice: str) -> list[str]:
    ledger = REPO_ROOT / "docs" / "UNIFIED_RETURN_PLATFORM_EXECUTION_STATE.md"
    if not ledger.exists():
        return [
            "execution ledger: docs/UNIFIED_RETURN_PLATFORM_EXECUTION_STATE.md does not exist"
        ]
    text = ledger.read_text(encoding="utf-8")
    if current_slice not in text:
        return [f"execution ledger: does not mention current slice '{current_slice}'"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scope-prefix",
        action="append",
        default=None,
        help="Expected staged-file path prefix(es); repeatable",
    )
    parser.add_argument(
        "--slice",
        default="3R",
        help="Current slice identifier expected in the execution ledger",
    )
    parser.add_argument(
        "--skip-cross-module-check",
        action="store_true",
        help="Skip the (slower) pytest-based architecture check",
    )
    args = parser.parse_args()

    problems: list[str] = []
    problems += check_branch()
    problems += check_remote_divergence()
    problems += check_unrelated_staged_files(args.scope_prefix)
    problems += check_diff_whitespace()

    added = staged_diff_added_lines()
    problems += check_production_placeholders(added)
    problems += check_lint_suppressions(added)
    problems += check_new_test_skips(added)
    problems += check_secret_looking_values(added)
    problems += check_physical_platform_literals(added)

    if not args.skip_cross_module_check:
        problems += check_cross_module_imports()

    problems += check_execution_ledger_current(args.slice)

    if problems:
        print(f"precommit_guard: {len(problems)} blocking issue(s) found:\n")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("precommit_guard: no blocking issues found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

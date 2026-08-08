"""Changed-scope gate runner (Slice 3R, §4.2/§14 of the execution directive).

Determines which gates are required from the actual changed-file set -- the UNION of
committed delta since a base commit, staged files, unstaged files, and untracked
non-ignored files (a diff against a base commit alone misses uncommitted worktree
changes, which is exactly what a pre-commit gate needs to see) -- and runs only the
commands relevant to what changed. Read-only with respect to source: it never modifies
files, only runs the project's existing quality commands.

Usage:
    python scripts/dev/run_changed_gate.py [--base <commit>] [--force]

Receipts are cached under the gitignored `.gate-receipts/` directory, keyed on
(HEAD/base identity, command, a digest of the files relevant to that command, and the
relevant tool/lockfile config). A cached PASS may be skipped; a FAIL is never cached.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RECEIPTS_DIR = REPO_ROOT / ".gate-receipts"


def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _redact(text: str) -> str:
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9\-\._~]+", r"\1[REDACTED]", text)
    text = re.sub(r"(mongodb://[^:]+:)[^@]+(@)", r"\1[REDACTED]\2", text)
    text = re.sub(r"(password=)[^\s]+", r"\1[REDACTED]", text)
    return text


def _git(args: list[str]) -> str:
    code, out = _run(["git", *args])
    if code != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out}")
    return out.strip()


def changed_files(base: str) -> set[str]:
    """Union of committed delta since base, staged, unstaged, and untracked
    non-ignored files -- NOT just `git diff base...HEAD`, which misses everything not
    yet committed."""
    files: set[str] = set()

    def _add_lines(output: str) -> None:
        for line in output.splitlines():
            line = line.strip()
            if line:
                files.add(line)

    try:
        _add_lines(_git(["diff", "--name-only", f"{base}...HEAD"]))
    except RuntimeError:
        pass  # base ref may not exist locally (e.g. shallow clone) -- degrade gracefully
    _add_lines(_git(["diff", "--name-only", "--cached"]))
    _add_lines(_git(["diff", "--name-only"]))
    _add_lines(_git(["ls-files", "--others", "--exclude-standard"]))
    return files


@dataclass
class GateCommand:
    name: str
    command: list[str]
    cwd: Path
    relevant_files: list[str] = field(default_factory=list)


def _matches(path: str, *prefixes: str) -> bool:
    return any(path.startswith(prefix) for prefix in prefixes)


def gates_for(files: set[str]) -> list[GateCommand]:
    backend = REPO_ROOT / "backend"
    gates: list[GateCommand] = []

    backend_py = sorted(
        f
        for f in files
        if _matches(f, "backend/src/", "backend/tests/")
        and f.endswith(".py")
        and (REPO_ROOT / f).is_file()  # a deleted file is still a "changed" file, but
        # ruff can't format/check something that no longer exists on disk.
    )
    if backend_py:
        rel = [f[len("backend/") :] for f in backend_py]
        gates.append(
            GateCommand(
                "backend-ruff-format",
                ["poetry", "run", "ruff", "format", "--check", *rel],
                backend,
                rel,
            )
        )
        gates.append(
            GateCommand(
                "backend-ruff-check",
                ["poetry", "run", "ruff", "check", *rel],
                backend,
                rel,
            )
        )
        gates.append(
            GateCommand("backend-mypy", ["poetry", "run", "mypy", "src"], backend, rel)
        )
        gates.append(
            GateCommand(
                "backend-compileall",
                ["poetry", "run", "python", "-m", "compileall", "-q", "src"],
                backend,
                rel,
            )
        )
        gates.append(
            GateCommand(
                "backend-import-check",
                ["poetry", "run", "python", "-c", "import return_platform.main"],
                backend,
                rel,
            )
        )

    frontend_files = sorted(f for f in files if f.startswith("frontend/src/"))
    if frontend_files:
        frontend = REPO_ROOT / "frontend"
        gates.append(
            GateCommand(
                "frontend-lint", ["npm", "run", "lint"], frontend, frontend_files
            )
        )
        gates.append(
            GateCommand(
                "frontend-typecheck",
                ["npm", "run", "typecheck"],
                frontend,
                frontend_files,
            )
        )
        gates.append(
            GateCommand(
                "frontend-build", ["npm", "run", "build"], frontend, frontend_files
            )
        )

    contract_relevant = [
        f
        for f in files
        if _matches(f, "backend/src/return_platform/api/", "backend/openapi/")
        or f.endswith("openapi.json")
    ]
    if contract_relevant:
        gates.append(
            GateCommand(
                "openapi-drift",
                # check_openapi_drift.py imports return_platform.main, so it needs an
                # interpreter with the backend's dependencies installed -- `poetry -C
                # backend run` gives it that. `-C backend` also changes the *subprocess's*
                # cwd to backend/, so the script path must be absolute (it lives at the
                # repo root, not under backend/).
                [
                    "poetry",
                    "-C",
                    "backend",
                    "run",
                    "python",
                    str(REPO_ROOT / "scripts" / "check_openapi_drift.py"),
                ],
                REPO_ROOT,
                contract_relevant,
            )
        )

    config_relevant = [f for f in files if f.startswith("backend/config/")]
    if config_relevant:
        gates.append(
            GateCommand(
                "canonical-config-validation",
                [
                    "poetry",
                    "run",
                    "pytest",
                    "tests/configuration/test_canonical_application.py",
                    "-q",
                ],
                backend,
                config_relevant,
            )
        )

    infra_relevant = [
        f
        for f in files
        if f == "compose.yaml" or _matches(f, "scripts/linux/", "scripts/windows/")
    ]
    if infra_relevant:
        gates.append(
            GateCommand(
                "compose-config",
                ["docker", "compose", "config", "-q"],
                REPO_ROOT,
                infra_relevant,
            )
        )

    architecture_relevant = [
        f
        for f in files
        if _matches(
            f,
            "backend/src/return_platform/platform/",
            "backend/src/return_platform/agents/",
        )
        or "test_no_module_cross_imports" in f
        or "test_no_cross_agent_imports" in f
        or "test_context_has_no_module_fields" in f
    ]
    if architecture_relevant:
        gates.append(
            GateCommand(
                "architecture-invariants",
                [
                    "poetry",
                    "run",
                    "pytest",
                    "tests/platform/test_no_module_cross_imports.py",
                    "tests/platform/test_layering.py",
                    "tests/agents/test_no_cross_agent_imports.py",
                    "tests/agents/test_context_has_no_module_fields.py",
                    "-q",
                ],
                backend,
                architecture_relevant,
            )
        )

    return gates


def _file_digest(paths: list[str]) -> str:
    hasher = hashlib.sha256()
    for rel_path in sorted(paths):
        full = (
            REPO_ROOT / rel_path
            if not rel_path.startswith("backend/")
            else REPO_ROOT / rel_path
        )
        try:
            hasher.update(full.read_bytes())
        except OSError:
            hasher.update(b"<missing>")
        hasher.update(rel_path.encode("utf-8"))
    return hasher.hexdigest()[:16]


def _lockfile_digest() -> str:
    hasher = hashlib.sha256()
    for name in (
        "backend/poetry.lock",
        "backend/uv.lock",
        "frontend/package-lock.json",
    ):
        path = REPO_ROOT / name
        if path.exists():
            hasher.update(path.read_bytes())
    return hasher.hexdigest()[:16]


def receipt_path(gate: GateCommand, head: str) -> Path:
    key = hashlib.sha256(
        json.dumps(
            {
                "gate": gate.name,
                "head": head,
                "command": gate.command,
                "files_digest": _file_digest(gate.relevant_files),
                "lockfile_digest": _lockfile_digest(),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return RECEIPTS_DIR / f"{gate.name}-{key}.json"


def run_gate(gate: GateCommand, head: str, force: bool) -> bool:
    receipt_file = receipt_path(gate, head)
    if not force and receipt_file.exists():
        try:
            existing = json.loads(receipt_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        if existing and existing.get("status") == "PASS":
            print(f"[skip:cached] {gate.name}")
            return True

    code, output = _run(gate.command, cwd=gate.cwd)
    passed = code == 0
    RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    if passed:
        receipt_file.write_text(
            json.dumps(
                {"gate": gate.name, "status": "PASS", "command": gate.command}, indent=2
            ),
            encoding="utf-8",
        )
        print(f"[pass] {gate.name}")
    else:
        # Failures are never cached.
        print(f"[FAIL] {gate.name}")
        print(_redact(output)[-4000:])
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/refactor/unified-return-platform")
    parser.add_argument(
        "--force", action="store_true", help="Ignore cached PASS receipts"
    )
    args = parser.parse_args()

    try:
        head = _git(["rev-parse", "HEAD"])
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    files = changed_files(args.base)
    if not files:
        print("No changed files detected -- nothing to gate.")
        return 0

    gates = gates_for(files)
    if not gates:
        print(f"{len(files)} changed file(s), but none map to a known gate.")
        return 0

    print(f"Changed files: {len(files)}. Gates required: {[g.name for g in gates]}")

    all_passed = True
    for gate in gates:
        if not run_gate(gate, head, args.force):
            all_passed = False

    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

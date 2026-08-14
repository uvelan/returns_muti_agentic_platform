#!/usr/bin/env python3
"""Negative-control tests for scan_secrets.py.

A secret scanner that has only ever been run against a repository it passes is
a scanner nobody has tested. These tests plant credentials in a throwaway
repository and assert the scanner FAILS, assert it stays quiet on the shapes
that are supposed to be quiet, and assert the one property everything else
depends on: **the value is never printed**.

No pytest, no dependencies -- this runs on a bare runner and on a developer
laptop identically:

    python scripts/security/test_scan_secrets.py

Note: every planted credential is assembled at run time from fragments. A
complete credential pattern must never appear literally in a committed file, or
this test file would itself trip GitHub push protection and the working-tree
gate it exists to verify.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCANNER = Path(__file__).resolve().parent / "scan_secrets.py"

# Assembled at run time; see the module docstring. The lengths matter -- these
# have to match the real detection patterns exactly, or the test passes for the
# wrong reason.
#
# None of these constants may be NAMED `*_PASSWORD`, `*_SECRET`, `*_TOKEN` or
# `*_API_KEY`. This file is itself scanned by the working-tree gate, and a
# credential-named constant assigned a long literal at column 0 is
# indistinguishable from the real thing. The first version of this file used
# such a name and was blocked by the very rule it exists to test -- the correct
# outcome, so the constant is named for what it stands in for instead.
FAKE_NVIDIA = "nvapi" + "-" + ("Z9y8x7w6v5u4t3s2r1q0" * 2) + "pOnMlKjIhG"
FAKE_GOOGLE = "AIza" + "Sy" + ("B7f3K2m9Qw1eR4tY6uI8oP0aS2dF5gH1j" * 2)[:33]
FAKE_INFRA_CREDENTIAL = "Jq7!vZ2m" + "Xr9%tL4w" + "Ns6^yH1b" + "Ke8*cV3d" + "Pu5&gT0a"

assert len(FAKE_GOOGLE) == 39, len(FAKE_GOOGLE)
assert len(FAKE_NVIDIA) >= 26, len(FAKE_NVIDIA)
assert len(FAKE_INFRA_CREDENTIAL) >= 16, len(FAKE_INFRA_CREDENTIAL)

failures: list[str] = []


def run_scanner(*args: str) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(SCANNER), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return result.returncode, result.stdout.decode("utf-8", "replace")


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}{(' -- ' + detail) if detail else ''}")
        failures.append(name)


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def new_repo(root: Path) -> Path:
    repo = root / "fixture"
    repo.mkdir()
    git(repo.parent, "init", "-q", "fixture")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "secret scan test")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    return repo


def main() -> int:
    print("scan_secrets.py negative controls\n")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = new_repo(root)
        base = (
            subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True,
                stdout=subprocess.PIPE,
            )
            .stdout.decode()
            .strip()
        )

        # ---------------------------------------------------------------
        # 1. A provider key in a new commit must fail the range gate, and
        #    the log must not contain it.
        # ---------------------------------------------------------------
        (repo / "config.py").write_text(
            f'NVIDIA_KEY = "{FAKE_NVIDIA}"\n', encoding="utf-8"
        )
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "plant a provider key")

        code, out = run_scanner(
            "--repo",
            str(repo),
            "--mode",
            "range",
            "--range",
            f"{base}..HEAD",
            "--no-allowlist",
        )
        check("range mode blocks a planted NVIDIA key", code == 1, f"exit={code}")
        check("range mode names the rule", "nvidia-api-key" in out)
        check("range mode does NOT print the value", FAKE_NVIDIA not in out)

        # ---------------------------------------------------------------
        # 2. Same key, working-tree gate.
        # ---------------------------------------------------------------
        code, out = run_scanner(
            "--repo", str(repo), "--mode", "worktree", "--no-allowlist"
        )
        check("worktree mode blocks a planted provider key", code == 1, f"exit={code}")
        check("worktree mode does NOT print the value", FAKE_NVIDIA not in out)

        # ---------------------------------------------------------------
        # 3. An UNTRACKED file is deliberately out of scope. A developer's real
        #    `.env` is gitignored and full of live credentials; scanning it
        #    would fire on every local run until somebody disabled the gate.
        #    Staged files ARE in scope, which is what makes this usable as a
        #    pre-commit hook.
        # ---------------------------------------------------------------
        (repo / "config.py").unlink()
        git(repo, "add", "-A")
        (repo / ".env").write_text(
            f"MSSQL_SA_PASSWORD={FAKE_INFRA_CREDENTIAL}\n", encoding="utf-8"
        )
        code, out = run_scanner(
            "--repo", str(repo), "--mode", "worktree", "--no-allowlist"
        )
        check("an untracked local .env is out of scope", code == 0, f"exit={code}")
        (repo / ".env").unlink()

        # ---------------------------------------------------------------
        # 4. An infrastructure password -- no provider shape at all. This is
        #    the class GitHub push protection cannot catch, and the reason the
        #    six compose credentials leaked unnoticed.
        # ---------------------------------------------------------------
        (repo / "stack.env").write_text(
            f"MSSQL_SA_PASSWORD={FAKE_INFRA_CREDENTIAL}\n", encoding="utf-8"
        )
        git(repo, "add", "-A")
        code, out = run_scanner(
            "--repo", str(repo), "--mode", "worktree", "--no-allowlist"
        )
        check(
            "worktree mode blocks a shapeless infra password", code == 1, f"exit={code}"
        )
        check("infra password is NOT printed", FAKE_INFRA_CREDENTIAL not in out)

        # ---------------------------------------------------------------
        # 5. The correct patterns must stay quiet, or the gate gets disabled
        #    by whoever is tired of it.
        # ---------------------------------------------------------------
        (repo / "stack.env").write_text(
            "MSSQL_SA_PASSWORD=vault://secret/production/data-sources/sqlserver#password\n"
            "GRAPH_PASSWORD=${GRAPH_PASSWORD}\n"
            "MONGO_ROOT_PASSWORD=changeme\n"
            "VALKEY_PASSWORD=\n"
            "PLATFORM_NVIDIA_API_KEYS=[]\n"
            "PLATFORM_SUPPORT_TICKET_API_KEY=your-api-key-here\n",
            encoding="utf-8",
        )
        git(repo, "add", "-A")
        code, out = run_scanner(
            "--repo", str(repo), "--mode", "worktree", "--no-allowlist"
        )
        check(
            "vault refs, interpolation, placeholders and [] are quiet",
            code == 0,
            out.strip().splitlines()[-1] if out.strip() else "",
        )

        # ---------------------------------------------------------------
        # 6. The allowlist suppresses exactly its own digest and nothing else.
        # ---------------------------------------------------------------
        (repo / "stack.env").write_text(
            f"MSSQL_SA_PASSWORD={FAKE_INFRA_CREDENTIAL}\n", encoding="utf-8"
        )
        digest = hashlib.sha256(FAKE_INFRA_CREDENTIAL.encode()).hexdigest()[:16]
        allow_dir = repo / "scripts" / "security"
        allow_dir.mkdir(parents=True)
        (allow_dir / "known_exposures.json").write_text(
            json.dumps(
                {"known_exposures": [{"sha256_prefix": digest, "reason": "fixture"}]}
            ),
            encoding="utf-8",
        )
        git(repo, "add", "-A")

        code, out = run_scanner("--repo", str(repo), "--mode", "worktree")
        check("allowlisted digest passes", code == 0, f"exit={code}")
        check(
            "allowlisted finding is still reported as a known exposure",
            "KNOWN EXPOSURES" in out,
        )

        code, _ = run_scanner(
            "--repo", str(repo), "--mode", "worktree", "--no-allowlist"
        )
        check("--no-allowlist overrides the allowlist", code == 1, f"exit={code}")

        # A different secret must NOT inherit the allowlist entry.
        (repo / "stack.env").write_text(
            f"MSSQL_SA_PASSWORD={FAKE_INFRA_CREDENTIAL}\n"
            f"GRAPH_PASSWORD={FAKE_INFRA_CREDENTIAL[::-1]}\n",
            encoding="utf-8",
        )
        git(repo, "add", "-A")
        code, out = run_scanner("--repo", str(repo), "--mode", "worktree")
        check(
            "a different secret does not inherit the allowlist",
            code == 1,
            f"exit={code}",
        )

        # ---------------------------------------------------------------
        # 7. A key inside a committed archive is still a leaked key. This is
        #    how linux_kit/returns_platform.tar.gz had to be cleared.
        # ---------------------------------------------------------------
        import tarfile

        (repo / "stack.env").unlink()
        payload = root / "payload.env"
        payload.write_text(
            f'PLATFORM_GOOGLE_API_KEYS=["{FAKE_GOOGLE}"]\n', encoding="utf-8"
        )
        with tarfile.open(repo / "kit.tar.gz", "w:gz") as archive:
            archive.add(payload, arcname="kit/.env")
        git(repo, "add", "-A")
        code, out = run_scanner(
            "--repo", str(repo), "--mode", "worktree", "--no-allowlist"
        )
        check("a key inside a committed tarball is found", code == 1, f"exit={code}")
        check("archive finding does NOT print the value", FAKE_GOOGLE not in out)

        shutil.rmtree(repo, ignore_errors=True)

    # ------------------------------------------------------------------
    # A source alias is a reference, not a credential -- and the narrowing
    # that recognises it must not reach any further than source.
    #
    # `_LEGACY_FENCING_TOKEN = LEGACY_FENCING_TOKEN` (sync_service.py, from
    # GRAPH-01) failed the whole scan: 20 characters, 13 distinct, so length
    # and entropy could not reject it. The four controls below are what keep
    # the fix from becoming a hole -- the last two are the ones that matter.
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = new_repo(root)

        (repo / "sync_service.py").write_text(
            "LEGACY_FENCING_TOKEN = 1\n"
            "_LEGACY_FENCING_TOKEN = LEGACY_FENCING_TOKEN\n"
            "_ALIASED_SECRET = constants.SHARED_SECRET\n",
            encoding="utf-8",
        )
        git(repo, "add", "-A")
        code, out = run_scanner(
            "--repo", str(repo), "--mode", "worktree", "--no-allowlist"
        )
        check(
            "a bare-name alias in .py is not a credential",
            code == 0,
            f"exit={code} out={out.strip()[:200]}",
        )
        check(
            "a dotted-name alias in .py is not a credential", "SHARED_SECRET" not in out
        )

        # A QUOTED literal in source is still caught. This is how the live
        # provider keys entered history at 52732a5 -- hardcoded in conftest.py.
        (repo / "sync_service.py").write_text(
            f'_HARDCODED_TOKEN = "{FAKE_INFRA_CREDENTIAL}"\n', encoding="utf-8"
        )
        git(repo, "add", "-A")
        code, out = run_scanner(
            "--repo", str(repo), "--mode", "worktree", "--no-allowlist"
        )
        check("a QUOTED literal in .py is still blocked", code == 1, f"exit={code}")
        check("the hardcoded literal is NOT printed", FAKE_INFRA_CREDENTIAL not in out)

        # The scope check: an unquoted, identifier-shaped value in a `.env` is
        # what a real generated password looks like. If the narrowing leaked
        # out of source files, this is the control that fails.
        (repo / "sync_service.py").unlink()
        (repo / "stack.env").write_text(
            "MONGO_ROOT_PASSWORD=abc123def456ghi789jkl\n", encoding="utf-8"
        )
        git(repo, "add", "-A")
        code, out = run_scanner(
            "--repo", str(repo), "--mode", "worktree", "--no-allowlist"
        )
        check(
            "an identifier-shaped .env password is STILL blocked",
            code == 1,
            f"exit={code} -- the source narrowing must not reach .env files",
        )

        shutil.rmtree(repo, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        return 1
    print("all negative controls passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

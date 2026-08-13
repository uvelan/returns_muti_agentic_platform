#!/usr/bin/env python3
"""Build the `git filter-repo --replace-text` input for the SEC-01 purge.

The secret values are NOT stored in this repository. They are recovered at run
time from the historical blobs that already contain them, written to a file the
caller supplies (which must live outside the repository and be mode 0600), and
deleted by the caller as soon as filter-repo has consumed it.

Every recovered value is checked against scripts/security/known_exposures.json
by sha256 prefix. That check is the safety interlock of the whole purge:

  * a value that is NOT in the reviewed baseline aborts the run -- the purge
    never rewrites history around something a human has not signed off on;
  * a baseline entry that is NOT found in history aborts the run -- a purge
    that silently misses one of the nine credentials is worse than no purge,
    because it produces a "history is clean" claim that is false.

Usage:
    python scripts/security/build_replacements.py --out /secure/tmp/replacements.txt
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

# The blobs that carry the credentials. Both are pinned by commit so the input
# to the purge cannot drift when refs move.
SOURCE_BLOBS = (
    "bb9bf2e6acf041bf075797eafcbb9829cd4344f2:.env.vault-backup",
    "bb9bf2e6acf041bf075797eafcbb9829cd4344f2:backend/.env.vault-backup",
    "52732a5a8a76ef2311ad28c699c21654fbdb0b08:backend/tests/conftest.py",
)

# Not `$`-anchored, for the CRLF reason documented in scan_secrets.py: a purge
# that silently skips a value because of a carriage return is worse than no
# purge, because it still reports success.
SECRET_ASSIGNMENT = re.compile(
    r"^[ \t]*(?:export[ \t]+)?"
    r"([A-Za-z_][A-Za-z0-9_]*(?:PASSWORD|SECRET|TOKEN|REPLICA_SET_KEY))"
    r"[ \t]*=[ \t]*(\S[^\r\n]*)",
    re.MULTILINE,
)
PROVIDER_KEY = re.compile(r"nvapi-[A-Za-z0-9_\-]{20,}|AIza[0-9A-Za-z_\-]{35}")
NOT_A_SECRET = re.compile(
    r"^(?:vault:|vault://|\$\{|<|\[|changeme|placeholder|your[-_ ]|example)",
    re.IGNORECASE,
)

REPLACEMENT = "***SEC-01-PURGED-CREDENTIAL***"


def git_show(repo: Path, ref: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", ref],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", "replace")


def digest16(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def recover(repo: Path) -> dict[str, str]:
    """digest16 -> secret value. Held in memory; never logged."""
    recovered: dict[str, str] = {}
    for ref in SOURCE_BLOBS:
        body = git_show(repo, ref)
        if body is None:
            print(f"warning: {ref} is not reachable from this clone", file=sys.stderr)
            continue
        for match in PROVIDER_KEY.finditer(body):
            value = match.group(0)
            recovered[digest16(value)] = value
        for match in SECRET_ASSIGNMENT.finditer(body):
            value = match.group(2).strip().strip('"').strip("'").rstrip(",").strip()
            if len(value) < 16 or NOT_A_SECRET.match(value):
                continue
            recovered[digest16(value)] = value
    return recovered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="repository (or mirror) root")
    parser.add_argument("--out", required=True,
                        help="destination file, OUTSIDE the repository, mode 0600")
    parser.add_argument("--baseline",
                        default="scripts/security/known_exposures.json",
                        help="reviewed baseline to interlock against")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    out = Path(args.out).resolve()

    if out.is_relative_to(repo):
        print(f"error: --out {out} is inside the repository. The replacements "
              f"file contains every leaked credential in plaintext and must "
              f"never sit in a working tree.", file=sys.stderr)
        return 2

    baseline_path = Path(args.baseline)
    if not baseline_path.is_absolute():
        baseline_path = repo / baseline_path
    if not baseline_path.is_file():
        # A mirror clone has no working tree; the operator passes the baseline
        # from the reviewed checkout instead.
        print(f"error: baseline {baseline_path} not found. Pass --baseline "
              f"pointing at the reviewed known_exposures.json.", file=sys.stderr)
        return 2

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    expected = {
        entry["sha256_prefix"]: entry
        for entry in baseline.get("known_exposures", [])
    }
    if not expected:
        print("error: the baseline lists no known exposures. Either the purge "
              "has already run, or you are pointing at the post-purge file.",
              file=sys.stderr)
        return 2

    recovered = recover(repo)

    unreviewed = sorted(set(recovered) - set(expected))
    missing = sorted(set(expected) - set(recovered))

    if unreviewed:
        print("ABORT: history carries credential values that are not in the "
              "reviewed baseline. Review them, add them to "
              "known_exposures.json, then re-run.", file=sys.stderr)
        for key in unreviewed:
            value = recovered[key]
            print(f"  unreviewed sha256[:16]={key} "
                  f"prefix={value[:4]!r} len={len(value)}", file=sys.stderr)
        return 1

    if missing:
        print("ABORT: the baseline lists credentials this history does not "
              "contain. Purging against a stale baseline would report success "
              "without removing them.", file=sys.stderr)
        for key in missing:
            entry = expected[key]
            print(f"  missing sha256[:16]={key} "
                  f"({entry.get('provider')} / {entry.get('credential')})",
                  file=sys.stderr)
        return 1

    lines = [f"{value}==>{REPLACEMENT}" for value in recovered.values()]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        out.chmod(0o600)
    except OSError:
        # Windows/NTFS without POSIX permissions; the runbook covers ACLs.
        print("warning: could not set mode 0600; restrict access manually.",
              file=sys.stderr)

    print(f"wrote {len(lines)} replacement rules to {out}")
    for key in sorted(recovered):
        entry = expected[key]
        print(f"  sha256[:16]={key}  {entry.get('provider')} / "
              f"{entry.get('credential')}")
    print("\nThis file contains plaintext credentials. Delete it as soon as "
          "git filter-repo has consumed it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

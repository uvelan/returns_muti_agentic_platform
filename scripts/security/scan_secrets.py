#!/usr/bin/env python3
"""Dependency-free secret scanner for this repository.

Why this exists rather than only calling gitleaks: the secret scan has to be
runnable in three places that do not share a toolchain -- a GitHub runner, a
developer laptop, and an air-gapped review box. gitleaks is the primary engine
in CI (see .github/workflows/secret-scan.yml); this is the fallback that always
works, and it is also the tool the SEC-01 purge uses to *verify* that history is
clean afterwards.

It never prints a secret value. Every finding is reported as
provider + path + first four characters + length + sha256 prefix, which is
enough to identify and rotate a credential without copying it into a CI log,
a terminal transcript, or an issue tracker.

Modes
-----
  --mode worktree   scan the files in the working tree (default)
  --mode range      scan the blobs introduced by a commit range (pre-merge gate)
  --mode history    scan every blob reachable from every ref (release gate)

Exit codes
----------
  0  no findings (or only allowlisted ones)
  1  findings that are not allowlisted
  2  usage / environment error
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
from collections import defaultdict
from pathlib import Path

# --------------------------------------------------------------------------
# Detection rules
#
# These are value-shaped, not name-shaped: `API_KEY=` proves nothing, the shape
# of what follows does. Name-shaped heuristics live in ENV_ASSIGNMENT below and
# are deliberately narrower, because they are the noisy half.
# --------------------------------------------------------------------------

RULES: list[tuple[str, re.Pattern[bytes]]] = [
    ("nvidia-api-key", re.compile(rb"nvapi-[A-Za-z0-9_\-]{20,}")),
    ("google-api-key", re.compile(rb"AIza[0-9A-Za-z_\-]{35}")),
    ("openai-api-key", re.compile(rb"sk-(?:proj-)?[A-Za-z0-9_\-]{32,}")),
    ("anthropic-api-key", re.compile(rb"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("github-token", re.compile(rb"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("slack-token", re.compile(rb"xox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("aws-access-key-id", re.compile(rb"(?:AKIA|ASIA)[0-9A-Z]{16}")),
    ("vault-service-token", re.compile(rb"\bhv[sb]\.[A-Za-z0-9_\-]{20,}")),
    ("private-key-block",
     re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("json-web-token",
     re.compile(rb"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("stripe-key", re.compile(rb"[rs]k_(?:live|test)_[A-Za-z0-9]{24,}")),
    ("sendgrid-key", re.compile(rb"SG\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}")),
    ("google-oauth-client-secret", re.compile(rb"GOCSPX-[A-Za-z0-9_\-]{20,}")),
    ("npm-token", re.compile(rb"npm_[A-Za-z0-9]{36}")),
    ("azure-storage-key",
     re.compile(rb"DefaultEndpointsProtocol=https;AccountName=[^;]+;AccountKey=[A-Za-z0-9+/=]{40,}")),
]

# A `NAME=value` assignment where NAME claims to hold a credential and value is
# long enough and random enough to actually be one.
#
# Deliberately NOT anchored with `$`: a CRLF file leaves `\r` before the newline,
# `$` will not match there, and the rule would silently pass on every Windows
# checkout. This repository ships a .gitattributes, so CRLF working trees are
# normal here. `[^\r\n]*` already stops at the line end; the caller strips.
ENV_ASSIGNMENT = re.compile(
    rb"^[ \t]*(?:export[ \t]+)?"
    rb"([A-Za-z_][A-Za-z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|APIKEY|PRIVATE_KEY|REPLICA_SET_KEY))"
    rb"[ \t]*=[ \t]*(\S[^\r\n]*)",
    re.MULTILINE,
)

# Values that look secret-shaped but are declarations of intent, not secrets.
NOT_A_SECRET = re.compile(
    rb"^(?:"
    rb"vault:|vault://|\$\{|\$\(|<[^>]*>|\{\{|%[A-Z_]+%|"
    rb"changeme|change_me|placeholder|your[-_ ]|example|dummy|sample|redacted|"
    rb"test[-_]?(?:key|password|token|secret)|fake|noop|none|null|unset|"
    rb"\*+$|x+$|\.{2,}"
    rb")",
    re.IGNORECASE,
)

# Binary/vendored paths that cannot usefully hold a reviewable credential.
SKIP_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".webp", ".svg",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".pdf", ".zip", ".7z",
    ".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe", ".bin", ".wasm",
    ".mp4", ".webm", ".mp3", ".lock",
)
SKIP_DIR_PARTS = ("node_modules", ".git", "__pycache__", ".venv", "dist", "build")

ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar")

MAX_BLOB_BYTES = 8_000_000
MAX_ARCHIVE_MEMBER_BYTES = 4_000_000

ALLOWLIST_PATH = "scripts/security/known_exposures.json"


# --------------------------------------------------------------------------


class Finding:
    __slots__ = ("rule", "location", "value")

    def __init__(self, rule: str, location: str, value: bytes) -> None:
        self.rule = rule
        self.location = location
        self.value = value

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.value).hexdigest()

    def redacted(self) -> str:
        # sha256[:16] is exactly the key `known_exposures.json` is indexed by,
        # so a reviewer can copy it straight out of a CI log into an allowlist
        # entry. 16 hex characters identify a value without revealing it.
        text = self.value.decode("utf-8", "replace")
        return (
            f"prefix={text[:4]!r} len={len(text)} sha256[:16]={self.digest[:16]}"
        )

    def key(self) -> tuple[str, str]:
        return (self.location, self.digest[:16])


def git(repo: Path, args: list[str], stdin: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args[:3])} failed: "
            f"{result.stderr.decode('utf-8', 'replace').strip()[:300]}"
        )
    return result.stdout


def interesting_path(path: str) -> bool:
    lowered = path.lower()
    if lowered.endswith(SKIP_SUFFIXES):
        return False
    parts = lowered.replace("\\", "/").split("/")
    return not any(part in SKIP_DIR_PARTS for part in parts)


def scan_bytes(data: bytes, location: str) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[tuple[str, bytes]] = set()

    for rule, pattern in RULES:
        for match in pattern.finditer(data):
            value = match.group(0)
            if NOT_A_SECRET.match(value):
                continue
            if (rule, value) in seen:
                continue
            seen.add((rule, value))
            findings.append(Finding(rule, location, value))

    for match in ENV_ASSIGNMENT.finditer(data):
        name = match.group(1).decode("ascii", "replace")
        # `.strip()` also removes the trailing `\r` a CRLF file leaves behind.
        value = match.group(2).strip().strip(b'"').strip(b"'").rstrip(b",").strip()
        if len(value) < 16 or NOT_A_SECRET.match(value):
            continue
        # An empty JSON array/object is the "no key configured" encoding.
        if value in (b"[]", b"{}", b'""', b"''"):
            continue
        # Reject low-entropy prose: a real credential is not four English words.
        if len(set(value)) < 8:
            continue
        rule = f"env-assignment:{name}"
        if (rule, value) in seen:
            continue
        seen.add((rule, value))
        findings.append(Finding(rule, location, value))

    return findings


def scan_archive(data: bytes, location: str) -> list[Finding]:
    findings: list[Finding] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            for member in archive:
                if not member.isfile() or member.size > MAX_ARCHIVE_MEMBER_BYTES:
                    continue
                if not interesting_path(member.name):
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                findings.extend(
                    scan_bytes(handle.read(), f"{location}::{member.name}")
                )
    except tarfile.TarError:
        # Not a readable archive; the outer caller already scanned it as bytes.
        pass
    return findings


def scan_worktree(repo: Path) -> list[Finding]:
    findings: list[Finding] = []
    listing = git(repo, ["ls-files", "-z"]).split(b"\0")
    for raw in listing:
        if not raw:
            continue
        path = raw.decode("utf-8", "replace")
        if not interesting_path(path):
            continue
        full = repo / path
        try:
            if not full.is_file() or full.stat().st_size > MAX_BLOB_BYTES:
                continue
            data = full.read_bytes()
        except OSError:
            continue
        findings.extend(scan_bytes(data, path))
        if path.lower().endswith(ARCHIVE_SUFFIXES):
            findings.extend(scan_archive(data, path))
    return findings


def _blob_paths(repo: Path, rev_args: list[str]) -> dict[bytes, set[str]]:
    paths: dict[bytes, set[str]] = defaultdict(set)
    for line in git(repo, ["rev-list", *rev_args, "--objects"]).splitlines():
        sha, _, path = line.partition(b" ")
        if not path:
            continue
        decoded = path.decode("utf-8", "replace")
        if interesting_path(decoded):
            paths[sha].add(decoded)
    return paths


def scan_blobs(repo: Path, rev_args: list[str]) -> list[Finding]:
    paths = _blob_paths(repo, rev_args)
    if not paths:
        return []

    check = git(
        repo,
        ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        stdin=b"\n".join(paths) + b"\n",
    )
    wanted: list[bytes] = []
    for line in check.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[1] == b"blob" and int(fields[2]) <= MAX_BLOB_BYTES:
            wanted.append(fields[0])
    if not wanted:
        return []

    stream = git(repo, ["cat-file", "--batch"], stdin=b"\n".join(wanted) + b"\n")

    findings: list[Finding] = []
    offset = 0
    while offset < len(stream):
        newline = stream.find(b"\n", offset)
        if newline == -1:
            break
        header = stream[offset:newline].split()
        if len(header) != 3:
            break
        sha, size = header[0], int(header[2])
        body = stream[newline + 1: newline + 1 + size]
        offset = newline + 1 + size + 1

        # A blob can live at several paths; name them all so the purge knows
        # every path it must rewrite.
        location = ",".join(sorted(paths.get(sha, {sha.decode()})))
        findings.extend(scan_bytes(body, location))
        if location.lower().endswith(ARCHIVE_SUFFIXES):
            findings.extend(scan_archive(body, location))
    return findings


def load_allowlist(repo: Path) -> tuple[set[str], dict[str, str]]:
    """Return (allowed sha256[:16] set, digest -> reason)."""
    path = repo / ALLOWLIST_PATH
    if not path.is_file():
        return set(), {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    allowed: set[str] = set()
    reasons: dict[str, str] = {}
    for entry in payload.get("known_exposures", []):
        digest = entry["sha256_prefix"]
        allowed.add(digest)
        reasons[digest] = entry.get("reason", "")
    return allowed, reasons


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("worktree", "range", "history"),
                        default="worktree")
    parser.add_argument("--range", dest="rev_range",
                        help="commit range for --mode range, e.g. BASE..HEAD")
    parser.add_argument("--repo", default=".", help="repository root")
    parser.add_argument("--no-allowlist", action="store_true",
                        help="report known exposures as failures too")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    if not (repo / ".git").exists():
        print(f"error: {repo} is not a git repository", file=sys.stderr)
        return 2

    if args.mode == "worktree":
        findings = scan_worktree(repo)
        scope = "tracked working-tree files"
    elif args.mode == "range":
        if not args.rev_range:
            print("error: --mode range requires --range", file=sys.stderr)
            return 2
        # `--not <base>` semantics: only objects the range introduces.
        base, _, head = args.rev_range.partition("..")
        rev_args = [head or "HEAD", "--not", base] if base else [head or "HEAD"]
        findings = scan_blobs(repo, rev_args)
        scope = f"blobs introduced by {args.rev_range}"
    else:
        findings = scan_blobs(repo, ["--all"])
        scope = "every blob reachable from every ref"

    allowed, reasons = (set(), {}) if args.no_allowlist else load_allowlist(repo)

    deduped: dict[tuple[str, str], Finding] = {}
    for finding in findings:
        deduped.setdefault(finding.key(), finding)

    blocking = [f for f in deduped.values() if f.digest[:16] not in allowed]
    known = [f for f in deduped.values() if f.digest[:16] in allowed]

    print(f"secret scan: {scope}")
    print(f"  findings: {len(deduped)}  blocking: {len(blocking)}  "
          f"known-exposure: {len(known)}")

    if known:
        print("\nKNOWN EXPOSURES (allowlisted, pending the SEC-01 history purge)")
        for finding in sorted(known, key=lambda f: (f.rule, f.location)):
            print(f"  [{finding.rule}] {finding.location}")
            print(f"      {finding.redacted()}")
            reason = reasons.get(finding.digest[:16])
            if reason:
                print(f"      reason: {reason}")

    if blocking:
        print("\nBLOCKING FINDINGS -- a credential must never enter this repository")
        for finding in sorted(blocking, key=lambda f: (f.rule, f.location)):
            print(f"  [{finding.rule}] {finding.location}")
            print(f"      {finding.redacted()}")
        print(
            "\nThe value itself is deliberately not printed. To resolve:\n"
            "  1. Revoke the credential at its provider console FIRST.\n"
            "  2. Remove it from the change; load it from Vault instead\n"
            "     (see scripts/vault/export_runtime_vault_env.sh).\n"
            "  3. If it already reached a pushed branch, follow\n"
            "     scripts/security/SEC-01_HISTORY_PURGE_RUNBOOK.md.\n"
            "  4. Only if this is a false positive, add it to\n"
            f"     {ALLOWLIST_PATH} with a reason and a reviewer."
        )
        return 1

    print("\nOK: no unreviewed credential-shaped values found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

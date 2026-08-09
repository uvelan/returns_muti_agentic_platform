"""OpenAPI zero-drift contract check.

Run with no arguments to **check** (writes nothing, exits 1 on drift). Run with
`--write` to **regenerate** every committed snapshot.

Three defects in the previous version, all of which made it untrustworthy as a
gate:

1. **It was a checker that mutated.** On drift it reported failure *and* wrote
   the new content, so the second run always passed. A gate that fixes what it
   is meant to detect cannot fail twice for the same reason, and locally it
   silently rewrote committed files a developer had not chosen to change.
2. **It deleted `frontend/openapi/return-platform.openapi.json` unconditionally
   and never regenerated it** -- a file `frontend/package.json`'s
   `contracts:generate` and `contracts:check` both consume.
3. **It covered two of five committed artifacts.** `backend/openapi/...`,
   `frontend/openapi/...` and the root `openapi.json` were unmanaged and had
   already diverged.

The deeper cause of the divergence was that two generators existed and built the
app with different settings, so the same commit produced different documents.
There is now one generator (`backend/scripts/export_openapi.py`) with pinned
contract settings, and this script imports it rather than reimplementing it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
EVIDENCE_DIR = ROOT / "docs/evidence/stage4_contract_closure"

#: Every committed copy of the document. They are duplicates of one artifact --
#: removing the duplication is Wave G's cleanup -- but while they exist they
#: must agree, because each one is somebody's source of truth.
JSON_SNAPSHOTS = (
    ROOT / "openapi" / "return-platform.openapi.json",
    ROOT / "backend" / "openapi" / "return-platform.openapi.json",
    ROOT / "frontend" / "openapi" / "return-platform.openapi.json",
    ROOT / "openapi.json",
)
COMMITTED_TYPES = (
    ROOT / "frontend" / "src" / "api" / "generated" / "return-platform.d.ts"
)


def _generate_types(openapi_bytes: bytes, destination: Path) -> str | None:
    """Returns the generated text, or None with a reason printed on failure."""
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        tmp_openapi = tmp / "return-platform.openapi.json"
        tmp_openapi.write_bytes(openapi_bytes)
        tmp_dts = tmp / "return-platform.d.ts"
        cmd = [
            "node",
            str(ROOT / "frontend/node_modules/openapi-typescript/bin/cli.js"),
            str(tmp_openapi),
            "--output",
            str(tmp_dts),
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=180,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            print("TS_GEN_TIMEOUT", file=sys.stderr)
            return None
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.returncode != 0 or not tmp_dts.exists():
            print(f"TS_GEN_FAILED: {result.returncode}", file=sys.stderr)
            return None
        return tmp_dts.read_text(encoding="utf-8")


def _normalized(text: str) -> str:
    """Line endings only. The repository is worked on from Windows and Linux,
    and a CRLF difference is not a contract change."""
    return text.replace("\r\n", "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="regenerate every committed snapshot instead of only checking them",
    )
    args = parser.parse_args()

    started = datetime.now(UTC).isoformat()
    sys.path.insert(0, str(ROOT / "backend" / "scripts"))
    from export_openapi import build_openapi_bytes  # noqa: PLC0415 - path set above

    openapi_bytes = build_openapi_bytes()
    digest = hashlib.sha256(openapi_bytes).hexdigest()
    diffs: list[str] = []

    for snapshot in JSON_SNAPSHOTS:
        relative = snapshot.relative_to(ROOT).as_posix()
        if not snapshot.is_file():
            diffs.append(f"MISSING: {relative}")
        elif snapshot.read_bytes() != openapi_bytes:
            diffs.append(f"DRIFT: {relative}")
        else:
            continue
        if args.write:
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_bytes(openapi_bytes)

    generated = _generate_types(openapi_bytes, COMMITTED_TYPES)
    if generated is None:
        diffs.append("TS_GEN_UNAVAILABLE")
    elif not COMMITTED_TYPES.is_file():
        diffs.append("MISSING: frontend/src/api/generated/return-platform.d.ts")
        if args.write:
            COMMITTED_TYPES.parent.mkdir(parents=True, exist_ok=True)
            COMMITTED_TYPES.write_text(generated, encoding="utf-8")
    elif _normalized(COMMITTED_TYPES.read_text(encoding="utf-8")) != _normalized(
        generated
    ):
        diffs.append("DRIFT: frontend/src/api/generated/return-platform.d.ts")
        if args.write:
            COMMITTED_TYPES.write_text(generated, encoding="utf-8")

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    receipt = {
        "stage": "4E",
        "gate": "openapi_drift",
        "mode": "write" if args.write else "check",
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "environment": "local-contract-validation",
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "openapi_sha256": digest,
        "snapshots": [p.relative_to(ROOT).as_posix() for p in JSON_SNAPSHOTS],
        "diffs": diffs,
        # In --write mode a diff is the work performed, not a failure; the run
        # succeeds and the caller commits the result. In check mode any diff
        # fails, and nothing on disk changed, so re-running fails identically
        # until someone actually regenerates.
        "status": "PASS" if not diffs or args.write else "FAIL",
    }
    receipt["exit_code"] = 0 if receipt["status"] == "PASS" else 1
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=EVIDENCE_DIR
    ) as handle:
        json.dump(receipt, handle, indent=2)
    Path(handle.name).replace(EVIDENCE_DIR / "openapi_drift_receipt.json")
    print(json.dumps(receipt, indent=2))
    return int(receipt["exit_code"])


if __name__ == "__main__":
    sys.exit(main())

"""Enforce exact baseline disposition match."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ALLOWED_DECISIONS = {
    "ACCEPTED_EXISTING_DELTA",
    "REJECTED_RELEASE_ARTIFACT",
    "INTENTIONAL_DEFERRED_CHANGE",
}


def main():
    ev = Path("docs/evidence/stage4_contract_closure")
    sha = (ev / "git_sha.txt").read_text().splitlines()[0].strip()
    status_raw = (ev / "git_status_before.bin").read_bytes()
    disp_raw = json.loads((ev / "baseline_disposition.json").read_text())

    git_paths = set()
    records = status_raw.split(b"\0")
    i = 0
    while i < len(records) - 1:
        if not records[i]:
            break
        prefix = records[i][:2]
        git_paths.add(records[i][3:].decode("utf-8"))
        if b"R" in prefix or b"C" in prefix:
            i += 1
            git_paths.add(records[i].decode("utf-8"))
        i += 1

    disp_paths = set(disp_raw.keys())
    if git_paths != disp_paths:
        sys.stderr.write(
            f"FAIL: Paths mismatch.\nGit: {sorted(git_paths)}\nDisp: {sorted(disp_paths)}\n"
        )
        return 1

    for path, meta in disp_raw.items():
        if "decision" not in meta or "git_status" not in meta or not meta.get("reason"):
            return 1
        if meta["decision"] not in ALLOWED_DECISIONS:
            return 1

    receipt = {
        "stage": "4A",
        "gate": "validate_baseline",
        "commit": sha,
        "status": "PASS",
        "exit_code": 0,
        "started_at": datetime.now(UTC).isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
    }
    (ev / "baseline_validator_receipt.json").write_text(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

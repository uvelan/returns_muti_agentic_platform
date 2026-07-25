"""Verify contract validation and evaluate promotion."""

import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    ev_dir = Path("docs/evidence/stage4_contract_closure")
    source_commit = (ev_dir / "validated_source_commit.txt").read_text().strip()
    baseline_commit = (ev_dir / "git_sha.txt").read_text().splitlines()[0].strip()

    current_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()
    failures = []
    if current_head != source_commit:
        failures.append(
            f"HEAD {current_head} differs from validated source {source_commit}"
        )

    for stage in ["4A", "4B", "4C", "4D", "4E"]:
        receipt_file = ev_dir / f"stage_{stage.lower()}_gate.json"
        if not receipt_file.exists():
            failures.append(f"Missing {stage} aggregate receipt")
            continue
        try:
            data = json.loads(receipt_file.read_text())
        except Exception:
            failures.append(f"Malformed {stage} aggregate receipt")
            continue

        if data.get("overall") != "PASS":
            failures.append(f"{stage} failed")
        expected = baseline_commit if stage == "4A" else source_commit
        if data.get("commit") != expected:
            failures.append(
                f"{stage} commit mismatch: {data.get('commit')} != {expected}"
            )

    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-status", "-z"],
        capture_output=True,
        text=True,
    ).stdout.split("\0")
    mod = subprocess.run(
        ["git", "diff", "--name-status", "-z"], capture_output=True, text=True
    ).stdout.split("\0")
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        text=True,
    ).stdout.split("\0")

    def extract_paths(records):
        paths = set()
        i = 0
        while i < len(records) - 1:
            if not records[i]:
                break
            status = records[i]
            if status.startswith("R") or status.startswith("C"):
                i += 1
                paths.add(records[i])
            else:
                paths.add(records[i + 1])
            i += 2
        return list(paths)

    all_paths = extract_paths(staged) + extract_paths(mod) + [p for p in untracked if p]

    ev_full = ev_dir.resolve()
    for p in all_paths:
        if not (Path.cwd() / p).resolve().is_relative_to(ev_full):
            failures.append(f"Unexpected tree change: {p}")

    decision = "PROMOTE" if not failures else "BLOCK"
    receipt = {
        "target_classification": "CONTRACT_TESTED",
        "validated_source_commit": source_commit,
        "evidence_parent_commit": current_head,
        "decision": decision,
        "failures": failures,
    }
    (ev_dir / "contract_promotion.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt, indent=2))
    return 0 if decision == "PROMOTE" else 1


if __name__ == "__main__":
    sys.exit(main())

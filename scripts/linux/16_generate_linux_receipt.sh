#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
python3 - \
  "$EVIDENCE_DIR" \
  "$REPO_ROOT" \
  "$LINUX_SCRIPT_DIR/validation_phases.txt" \
  "$(repo_fingerprint)" <<'PY'
import json
import pathlib
import platform
import subprocess
import sys
from datetime import datetime, timezone

evidence = pathlib.Path(sys.argv[1])
repository = pathlib.Path(sys.argv[2])
phase_manifest = pathlib.Path(sys.argv[3])
current_fingerprint = sys.argv[4]
current_commit = subprocess.check_output(
    ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
).strip()
expected_phases = [
    pathlib.Path(line.strip()).stem
    for line in phase_manifest.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.lstrip().startswith("#")
]
if not expected_phases or len(expected_phases) != len(set(expected_phases)):
    raise SystemExit("Validation phase manifest is empty or contains duplicate phases.")

phases = []
missing_phases = []
invalid_phases = []
for phase in expected_phases:
    path = evidence / f"{phase}.json"
    if not path.is_file():
        missing_phases.append(phase)
        continue
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        invalid_phases.append(phase)
        continue
    if (
        not isinstance(value, dict)
        or value.get("phase") != phase
        or value.get("environment") != "linux"
        or value.get("commit") != current_commit
        or value.get("treeFingerprint") != current_fingerprint
    ):
        invalid_phases.append(phase)
        continue
    phases.append(value)

failed = next((item["phase"] for item in phases if item.get("status") != "PASS"), None)
overall_pass = not missing_phases and not invalid_phases and failed is None
payload = {
    "schemaVersion": 1,
    "generatedAt": datetime.now(timezone.utc).isoformat(),
    "environment": "linux",
    "distribution": platform.platform(),
    "kernel": platform.release(),
    "commit": current_commit,
    "treeFingerprint": current_fingerprint,
    "overallStatus": "PASS" if overall_pass else "FAIL",
    "failedPhase": failed,
    "expectedPhaseCount": len(expected_phases),
    "validatedPhaseCount": len(phases),
    "missingPhases": missing_phases,
    "invalidPhases": invalid_phases,
    "phases": phases,
    "linuxExecutionClaim": True,
}
target = evidence / "linux-validation-receipt.json"
target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(target)
if not overall_pass:
    raise SystemExit(1)
PY

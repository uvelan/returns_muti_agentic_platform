#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
python3 - "$EVIDENCE_DIR" <<'PY'
import datetime
import json
import pathlib
import platform
import subprocess
import sys

evidence = pathlib.Path(sys.argv[1])
phases = []
for path in sorted(evidence.glob("*.json")):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        continue
    if isinstance(value, dict) and "phase" in value and "status" in value:
        phases.append(value)
failed = next((item["phase"] for item in phases if item["status"] != "PASS"), None)
payload = {
    "schemaVersion": 1,
    "generatedAt": datetime.datetime.now(datetime.UTC).isoformat(),
    "environment": "linux",
    "distribution": platform.platform(),
    "kernel": platform.release(),
    "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "overallStatus": "FAIL" if failed else "PASS",
    "failedPhase": failed,
    "phases": phases,
    "linuxExecutionClaim": True,
}
target = evidence / "linux-validation-receipt.json"
target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(target)
PY

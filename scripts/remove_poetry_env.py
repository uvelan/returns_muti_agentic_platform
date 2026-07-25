import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def main():
    started = datetime.now(UTC).isoformat()
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        sha = "UNKNOWN"

    res = subprocess.run(
        ["python", "-m", "poetry", "--project", "backend", "env", "list"],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        exit_code = 1
    elif not res.stdout.strip():
        exit_code = 0
    else:
        rm = subprocess.run(
            ["python", "-m", "poetry", "--project", "backend", "env", "remove", "--all"]
        )
        exit_code = rm.returncode

    receipt = {
        "stage": "4B",
        "gate": "backend_env_remove",
        "commit": sha,
        "exit_code": exit_code,
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "status": "PASS" if exit_code == 0 else "FAIL",
    }
    Path(
        "docs/evidence/stage4_contract_closure/backend_env_remove_receipt.json"
    ).write_text(json.dumps(receipt, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

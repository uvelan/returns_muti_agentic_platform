import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def get_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    started = datetime.now(UTC).isoformat()
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        sha = "UNKNOWN"
    ev = Path("docs/evidence/stage4_contract_closure")

    b_before = (ev / "backend_lock_sha256_before.txt").read_text().strip()
    f_before = (ev / "frontend_lock_sha256_before.txt").read_text().strip()

    b_now = get_hash("backend/poetry.lock")
    f_now = get_hash("frontend/package-lock.json")

    exit_code = 0 if b_before == b_now and f_before == f_now else 1

    if exit_code == 0:
        res = subprocess.run(
            [
                "git",
                "diff",
                "--exit-code",
                "backend/pyproject.toml",
                "backend/poetry.lock",
                "frontend/package.json",
                "frontend/package-lock.json",
            ]
        )
        if res.returncode != 0:
            exit_code = 1

    receipt = {
        "stage": "4B",
        "gate": "lock_hash_compare",
        "commit": sha,
        "exit_code": exit_code,
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "status": "PASS" if exit_code == 0 else "FAIL",
    }
    (ev / "lock_hash_compare_receipt.json").write_text(json.dumps(receipt, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

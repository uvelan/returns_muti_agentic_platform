import argparse
import hashlib
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

MANIFEST = {
    "4A": {"baseline_validator_receipt.json": "validate_baseline"},
    "4B": {
        "backend_env_remove_receipt.json": "backend_env_remove",
        "backend_sync_receipt.json": "backend_sync",
        "frontend_ci_receipt.json": "frontend_ci",
        "frontend_playwright_receipt.json": "frontend_playwright",
        "backend_lock_diff_receipt.json": "backend_lock_diff",
        "frontend_lock_diff_receipt.json": "frontend_lock_diff",
        "lock_hash_compare_receipt.json": "lock_hash_compare",
    },
    "4C": {
        "pytest_collect_receipt.json": "pytest_collect",
        "backend_ruff_lint_receipt.json": "ruff_lint",
        "backend_ruff_format_receipt.json": "ruff_format",
        "backend_mypy_receipt.json": "mypy_strict",
        "backend_pytest_receipt.json": "pytest_cov",
    },
    "4D": {
        "frontend_lint_receipt.json": "lint",
        "frontend_typecheck_receipt.json": "typecheck",
        "frontend_tests_receipt.json": "test",
        "frontend_a11y_receipt.json": "a11y",
        "frontend_build_receipt.json": "build",
        "frontend_bundle_receipt.json": "check_bundle",
        "schema_registry_drift_receipt.json": "schema_registry_drift",
    },
    "4E": {
        "openapi_drift_receipt.json": "openapi_drift",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=MANIFEST.keys())
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    args = parser.parse_args()

    overall = "PASS"
    gates = {}

    for receipt_name, expected_gate in MANIFEST[args.stage].items():
        receipt_path = args.evidence_dir / receipt_name
        if not receipt_path.exists():
            print(f"FAIL: Missing {receipt_name}", file=sys.stderr)
            return 1
        try:
            raw_bytes = receipt_path.read_bytes()
            data = json.loads(raw_bytes)
        except Exception:
            print(f"FAIL: Malformed JSON in {receipt_name}", file=sys.stderr)
            return 1

        if data.get("stage") != args.stage or data.get("gate") != expected_gate:
            print(f"FAIL: {receipt_name} identity mismatch", file=sys.stderr)
            return 1

        if data.get("commit") != args.commit:
            print(
                f"FAIL: Commit mismatch in {receipt_name} ({data.get('commit')} != {args.commit})",
                file=sys.stderr,
            )
            return 1

        if data["status"] == "PASS" and data["exit_code"] != 0:
            print(
                f"FAIL: PASS with nonzero exit code in {receipt_name}", file=sys.stderr
            )
            return 1

        gates[data["gate"]] = {
            "exit_code": data["exit_code"],
            "status": data["status"],
            "hash": hashlib.sha256(raw_bytes).hexdigest(),
        }
        if data["status"] != "PASS":
            overall = "FAIL"

    if args.stage == "4C" and overall == "PASS":
        cov_path = args.evidence_dir / "backend_coverage.json"
        pct = (
            json.loads(cov_path.read_text()).get("totals", {}).get("percent_covered", 0)
            if cov_path.exists()
            else 0
        )
        if pct < 70.0:
            print(f"FAIL: Coverage {pct}% < 70%", file=sys.stderr)
            overall = "FAIL"

    out = {
        "stage": args.stage,
        "commit": args.commit,
        "evaluated_at": datetime.now(UTC).isoformat(),
        "gates": gates,
        "overall": overall,
    }
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=args.output.parent
    ) as tmp:
        json.dump(out, tmp, indent=2)
    Path(tmp.name).replace(args.output)
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

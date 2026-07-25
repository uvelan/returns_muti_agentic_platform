"""Deterministic OpenAPI zero-drift contract check."""
import hashlib, json, subprocess, sys, tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
COMMITTED_ROOT_OPENAPI = ROOT / "openapi" / "return-platform.openapi.json"
COMMITTED_TYPES = ROOT / "frontend" / "src" / "api" / "generated" / "return-platform.d.ts"
EVIDENCE_DIR = ROOT / "docs/evidence/stage4_contract_closure"

def build_contract_test_settings():
    from return_platform.configuration.settings import Settings
    return Settings.model_construct(
        environment="test",
        mongo_dsn="mongodb://localhost:27017",
        sqlserver_host="localhost",
        sqlserver_password="test",
        neo4j_password="test",
        frontend_cors_origin="http://localhost:3000"
    )

def main() -> int:
    # Delete frontend/openapi snapshot physically
    frontend_snap = ROOT / "frontend/openapi/return-platform.openapi.json"
    if frontend_snap.exists(): frontend_snap.unlink()

    started = datetime.now(UTC).isoformat()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT / "backend" / "src"))
    from return_platform.main import create_app

    app = create_app(custom_settings=build_contract_test_settings())
    schema = app.openapi()
    openapi_bytes = json.dumps(schema, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    openapi_digest = hashlib.sha256(openapi_bytes).hexdigest()
    diffs = []

    if not COMMITTED_ROOT_OPENAPI.is_file(): 
        diffs.append("MISSING_COMMITTED: root")
        COMMITTED_ROOT_OPENAPI.parent.mkdir(parents=True, exist_ok=True)
        COMMITTED_ROOT_OPENAPI.write_bytes(openapi_bytes)
    elif COMMITTED_ROOT_OPENAPI.read_bytes() != openapi_bytes: 
        diffs.append("DRIFT: root")
        COMMITTED_ROOT_OPENAPI.write_bytes(openapi_bytes)

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        tmp_openapi = tmp / "return-platform.openapi.json"
        tmp_openapi.write_bytes(openapi_bytes)
        tmp_dts = tmp / "return-platform.d.ts"
        cmd = ["node", str(ROOT / "frontend/node_modules/openapi-typescript/bin/cli.js"), str(tmp_openapi), "--output", str(tmp_dts)]
        
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
            sys.stdout.write(res.stdout); sys.stdout.write(res.stderr)
            if res.returncode != 0: diffs.append(f"TS_GEN_FAILED: {res.returncode}")
        except subprocess.TimeoutExpired:
            diffs.append("TS_GEN_TIMEOUT")

        if not COMMITTED_TYPES.is_file(): 
            diffs.append(f"MISSING_COMMITTED: TS types: {COMMITTED_TYPES}")
            if tmp_dts.exists():
                COMMITTED_TYPES.parent.mkdir(parents=True, exist_ok=True)
                COMMITTED_TYPES.write_text(tmp_dts.read_text(encoding="utf-8"), encoding="utf-8")
        elif tmp_dts.exists() and COMMITTED_TYPES.read_text(encoding="utf-8").replace("\r\n", "\n") != tmp_dts.read_text(encoding="utf-8").replace("\r\n", "\n"):
            diffs.append("DRIFT: generated TS types")
            COMMITTED_TYPES.write_text(tmp_dts.read_text(encoding="utf-8"), encoding="utf-8")

    receipt = {
        "stage": "4E", "gate": "openapi_drift",
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "environment": "local-contract-validation", "command": cmd,
        "started_at": started, "finished_at": datetime.now(UTC).isoformat(),
        "openapi_sha256": openapi_digest, "exit_code": 0 if not diffs else 1,
        "diffs": diffs, "status": "PASS" if not diffs else "FAIL"
    }
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=EVIDENCE_DIR) as tmp_rec:
        json.dump(receipt, tmp_rec, indent=2)
    Path(tmp_rec.name).replace(EVIDENCE_DIR / "openapi_drift_receipt.json")
    print(json.dumps(receipt, indent=2))
    return 0 if not diffs else 1

if __name__ == "__main__": sys.exit(main())

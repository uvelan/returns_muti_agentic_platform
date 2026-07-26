import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
RUN_GATE_SCRIPT = str(PROJECT_ROOT / "scripts" / "run_gate.py")

def test_run_gate_success(tmp_path):
    log_file = tmp_path / "test.log"
    receipt_file = tmp_path / "receipt.json"
    cmd = [
        sys.executable,
        RUN_GATE_SCRIPT,
        "--stage",
        "test",
        "--gate",
        "success",
        "--log",
        str(log_file),
        "--receipt",
        str(receipt_file),
        "--",
        sys.executable,
        "-c",
        "print('hello')",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0
    assert "hello" in log_file.read_text()
    receipt = json.loads(receipt_file.read_text())
    assert receipt["status"] == "PASS"


def test_run_gate_nonzero(tmp_path):
    log_file = tmp_path / "test.log"
    receipt_file = tmp_path / "receipt.json"
    cmd = [
        sys.executable,
        RUN_GATE_SCRIPT,
        "--stage",
        "test",
        "--gate",
        "fail",
        "--log",
        str(log_file),
        "--receipt",
        str(receipt_file),
        "--",
        sys.executable,
        "-c",
        "import sys; sys.exit(42)",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 42
    receipt = json.loads(receipt_file.read_text())
    assert receipt["status"] == "FAIL"


def test_run_gate_missing_executable(tmp_path):
    log_file = tmp_path / "test.log"
    receipt_file = tmp_path / "receipt.json"
    cmd = [
        sys.executable,
        RUN_GATE_SCRIPT,
        "--stage",
        "test",
        "--gate",
        "fail",
        "--log",
        str(log_file),
        "--receipt",
        str(receipt_file),
        "--",
        "doesnotexistcommand1234",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode != 0
    receipt = json.loads(receipt_file.read_text())
    assert receipt["status"] == "FAIL"


def test_run_gate_timeout(tmp_path):
    log_file = tmp_path / "test.log"
    receipt_file = tmp_path / "receipt.json"
    cmd = [
        sys.executable,
        RUN_GATE_SCRIPT,
        "--stage",
        "test",
        "--gate",
        "timeout",
        "--timeout",
        "1",
        "--log",
        str(log_file),
        "--receipt",
        str(receipt_file),
        "--",
        sys.executable,
        "-c",
        "import time; time.sleep(10)",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 124
    receipt = json.loads(receipt_file.read_text())
    assert receipt["status"] == "FAIL"
    assert "timed out" in receipt["error"]


def test_run_gate_redaction(tmp_path):
    log_file = tmp_path / "test.log"
    receipt_file = tmp_path / "receipt.json"
    env = os.environ.copy()
    env["PLATFORM_MONGO_DSN"] = "mongodb://user:supersecret@localhost"
    env["API_KEY"] = "my_secret_api_key_123"

    cmd = [
        sys.executable,
        RUN_GATE_SCRIPT,
        "--stage",
        "test",
        "--gate",
        "redact",
        "--log",
        str(log_file),
        "--receipt",
        str(receipt_file),
        "--",
        sys.executable,
        "-c",
        "import os; print('connecting to ' + os.environ.get('PLATFORM_MONGO_DSN', '')); print('key is ' + os.environ.get('API_KEY', ''))",  # noqa: E501
    ]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert res.returncode == 0
    log = log_file.read_text()
    assert "supersecret" not in log
    assert "my_secret_api_key_123" not in log
    assert "[REDACTED]" in log


def test_run_gate_empty_command(tmp_path):
    log_file = tmp_path / "test.log"
    receipt_file = tmp_path / "receipt.json"
    cmd = [
        sys.executable,
        RUN_GATE_SCRIPT,
        "--stage",
        "test",
        "--gate",
        "empty",
        "--log",
        str(log_file),
        "--receipt",
        str(receipt_file),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode != 0
    receipt = json.loads(receipt_file.read_text())
    assert receipt["status"] == "FAIL"

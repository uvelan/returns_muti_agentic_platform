import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _redact(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9\-\._~]+", r"\1[REDACTED]", text)
    text = re.sub(r"(mongodb://[^:]+:)[^@]+(@)", r"\1[REDACTED]\2", text)
    text = re.sub(r"(password=)[^\s]+", r"\1[REDACTED]", text)
    text = re.sub(r'(api_key["\']?\s*:\s*["\'])[^\'"]+(["\'])', r"\1[REDACTED]\2", text)
    text = re.sub(r"([?&]token=)[^&\s]+", r"\1[REDACTED]", text)
    text = re.sub(r"(Server=tcp:)[^;]+(;)", r"\1[REDACTED]\2", text)
    for key, val in os.environ.items():
        if (
            "PASSWORD" in key
            or "SECRET" in key
            or "KEY" in key
            or "TOKEN" in key
            or "API_KEY" in key
        ):
            if len(val) > 3:
                text = text.replace(val, "[REDACTED]")
    return text


def _stream_reader(pipe, log_file, errors):
    try:
        for line in iter(pipe.readline, ""):
            redacted = _redact(line)
            sys.stdout.write(redacted)
            log_file.write(redacted)
    except Exception as e:
        errors.append(e)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True)
    parser.add_argument("--gate", required=True)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    command = (
        args.command[1:] if args.command and args.command[0] == "--" else args.command
    )

    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)

    started = _utc_now()
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        sha = "UNKNOWN_GIT_ERROR"

    if not command:
        receipt = {
            "stage": args.stage,
            "gate": args.gate,
            "commit": sha,
            "environment": "local-contract-validation",
            "command": [],
            "exit_code": 1,
            "started_at": started,
            "finished_at": _utc_now(),
            "status": "FAIL",
            "error": "Empty command provided",
        }
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", delete=False, dir=args.receipt.parent
        ) as tmp_rec:
            json.dump(receipt, tmp_rec, indent=2)
        Path(tmp_rec.name).replace(args.receipt)
        args.log.write_text("[run_gate] Empty command provided\n")
        sys.exit(1)

    exit_code = 1
    error_message = None
    reader_errors = []
    tmp_log_name = None
    try:
        tmp_log = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", delete=False, dir=args.log.parent
        )
        tmp_log_name = tmp_log.name
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            if sys.platform == "win32"
            else 0,
            start_new_session=sys.platform != "win32",
            bufsize=1,
        )
        reader_thread = threading.Thread(
            target=_stream_reader, args=(proc.stdout, tmp_log, reader_errors)
        )
        reader_thread.start()

        try:
            proc.wait(timeout=args.timeout)
            exit_code = proc.returncode
            if reader_errors:
                error_message = f"Reader thread failed: {reader_errors[0]}"
                exit_code = 1
        except subprocess.TimeoutExpired:
            error_message = f"Process timed out after {args.timeout}s"
            exit_code = 124
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True
                )
            else:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        except KeyboardInterrupt:
            error_message = "KeyboardInterrupt"
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True
                )
            else:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise
        finally:
            reader_thread.join()
            if error_message:
                tmp_log.write(f"\n[run_gate] {error_message}\n")
            tmp_log.close()
            Path(tmp_log_name).replace(args.log)
        try:
            tmp_log.close()
        except Exception:
            pass
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        try:
            tmp_log.close()
        except Exception:
            pass
    finally:
        if tmp_log_name and Path(tmp_log_name).exists() and not args.log.exists():
            Path(tmp_log_name).replace(args.log)
        if not args.log.exists():
            args.log.write_text(f"[run_gate] Failed to create log. {error_message}\n")

        receipt = {
            "stage": args.stage,
            "gate": args.gate,
            "commit": sha,
            "environment": "local-contract-validation",
            "command": [_redact(c) for c in command],
            "exit_code": exit_code,
            "started_at": started,
            "finished_at": _utc_now(),
            "status": "PASS" if exit_code == 0 else "FAIL",
        }
        if error_message:
            receipt["error"] = _redact(error_message)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", delete=False, dir=args.receipt.parent
        ) as tmp_rec:
            json.dump(receipt, tmp_rec, indent=2)
        Path(tmp_rec.name).replace(args.receipt)
        sys.exit(exit_code)


if __name__ == "__main__":
    main()

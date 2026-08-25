"""TC-E2E-02 flow driver: sends turns and captures raw API evidence per run.

Commands:
  driver.py new                         -> prints a fresh conversation id
  driver.py send <conv-id> <message>    -> POST a turn, print + archive result
  driver.py transcript <conv-id>        -> GET transcript
  driver.py case <case-id>              -> GET case projection
  driver.py cases                       -> list cases

Env: TCE2E02_RUN_DIR (evidence archive), BASE (default http://localhost:8000).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = os.environ.get("TCE2E02_BASE", "http://localhost:8000")
RUN_DIR = Path(os.environ.get("TCE2E02_RUN_DIR", "evidence/TC-E2E-02/run-current"))
AGENT_ID = "order-discovery-agent"


def call(method: str, path: str, body: dict | None = None, timeout: float = 420.0) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as caught:
        detail = caught.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {caught.code} {path}\n{detail[:2000]}")


def archive(name: str, payload: dict) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H%M%S")
    (RUN_DIR / f"{stamp}_{name}.json").write_text(
        json.dumps(payload, indent=1, default=str), encoding="utf-8")


def send(conversation_id: str, message: str, version: int | None = None) -> dict:
    if version is None:
        try:
            transcript = call(
                "GET", f"/api/v2/order-agent/conversations/{conversation_id}/transcript")
            version = (transcript.get("data") or {}).get("conversationVersion", 0)
        except SystemExit:
            version = 0  # a fresh conversation the first turn creates
    turn_id = f"qa-{uuid.uuid4()}"
    result = call(
        "POST", f"/api/v2/order-agent/conversations/{conversation_id}/turns",
        {
            "conversation_id": conversation_id,
            "expected_conversation_version": version,
            "client_turn_id": turn_id,
            "idempotency_key": turn_id,
            "message_id": turn_id,
            "message": message,
            "agent_id": AGENT_ID,
            "session_timezone": "UTC",
        },
    )
    archive("turn", {"sent": message, "result": result})
    return result


def main() -> None:
    command = sys.argv[1]
    if command == "new":
        print(f"disc-{uuid.uuid4()}")
    elif command == "send":
        result = send(sys.argv[2], sys.argv[3])
        data = result.get("data") or {}
        response = data.get("response") or {}
        print("version:", data.get("conversation_version"),
              "case:", data.get("case_id"),
              "status:", response.get("status"))
        for statement in response.get("statements") or []:
            print("  •", statement.get("statement_type"), "-", statement.get("text"))
        if response.get("requested_input"):
            print("  ? ", response["requested_input"])
        evidence = data.get("query_evidence") or []
        for record in evidence:
            result_body = record.get("result")
            if isinstance(result_body, dict) and "candidates" in result_body:
                print("  candidates:", len(result_body["candidates"] or []),
                      "total_found:", result_body.get("total_found"))
    elif command == "transcript":
        payload = call("GET", f"/api/v2/order-agent/conversations/{sys.argv[2]}/transcript")
        archive("transcript", payload)
        print(json.dumps(payload, indent=1, default=str)[:4000])
    elif command == "case":
        payload = call("GET", f"/api/cases/{sys.argv[2]}")
        archive("case", payload)
        print(json.dumps(payload, indent=1, default=str)[:4000])
    elif command == "cases":
        payload = call("GET", "/api/cases")
        print(json.dumps(payload, indent=1, default=str)[:3000])
    else:
        raise SystemExit(f"unknown command {command}")


if __name__ == "__main__":
    main()

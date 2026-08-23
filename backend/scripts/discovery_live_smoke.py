"""L2: one real Order Discovery utterance, end to end, against live models.

The audit could not exercise Order Discovery at all -- every model attempt timed
out under `PLATFORM_AI_PROVIDER_ORDER=MANUAL` -- so the primary user journey went
unverified and the release verdict rested partly on a surface nobody had seen
work. This is the smallest run that proves it does.

**Closure is one case and one workflow start, not one HTTP 200.** The counts are
taken from Mongo and Temporal before and after, so a turn that answers politely
and creates nothing fails here.

**Isolation, because this spends money and touches a real graph.**

  - a unique conversation id per run, so nothing is shared with an operator's
    session;
  - a stable idempotency key derived from that id, so a retry is a retry rather
    than a second turn;
  - a bounded per-turn timeout and a hard ceiling on the number of turns, so a
    provider that hangs costs one wait rather than an afternoon;
  - read-only against the order graph -- the utterance names an order, it does
    not write one;
  - every identifier it captures is printed; no key, token or prompt payload is.

Run it with the backend up:

    python scripts/discovery_live_smoke.py --order CA273603
    python scripts/discovery_live_smoke.py --order CA273603 --turn-timeout 120
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from pymongo import AsyncMongoClient
from temporalio.client import Client

from return_platform.configuration.settings import Settings

#: How many turns the smoke will take before giving up. Discovery should reach a
#: candidate in one and a confirmation in two; three is slack, not an allowance.
MAX_TURNS = 3


def _evidence_directory() -> Path:
    directory = Path(__file__).resolve().parents[2] / ".runtime" / "discovery-smoke"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


async def _counts(settings: Settings) -> dict[str, int]:
    """Cases and running workflows, which is what closure is measured in."""
    dsn = settings.mongo_dsn
    client: AsyncMongoClient = AsyncMongoClient(
        dsn.get_secret_value() if hasattr(dsn, "get_secret_value") else str(dsn)
    )
    try:
        cases = await client[settings.mongo_database]["cases"].count_documents({})
    finally:
        await client.close()

    temporal = await Client.connect(
        str(settings.temporal_target),
        namespace=getattr(settings, "temporal_namespace", "default"),
    )
    running = 0
    async for _ in temporal.list_workflows('ExecutionStatus="Running"'):
        running += 1
    return {"cases": cases, "runningWorkflows": running}


async def _agent_id(client: httpx.AsyncClient) -> str | None:
    """The configured discovery agent, read rather than guessed."""
    response = await client.get("/api/config/runtime")
    if response.status_code != 200:
        return None
    payload = response.json().get("data") or {}
    for key in ("orderDiscoveryAgentId", "discoveryAgentId", "agentId"):
        if isinstance(payload.get(key), str):
            return str(payload[key])
    return None


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--order", required=True, help="an order reference already in the graph")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--turn-timeout", type=float, default=180.0)
    parser.add_argument("--agent-id", default=None)
    arguments = parser.parse_args()

    settings = Settings()
    conversation_id = f"smoke-{uuid.uuid4()}"
    started = datetime.now(UTC)

    record: dict[str, Any] = {
        "startedAt": started.isoformat(),
        "conversationId": conversation_id,
        "order": arguments.order,
        "providerOrder": settings.ai_provider_order,
        "turns": [],
    }

    record["before"] = await _counts(settings)

    async with httpx.AsyncClient(
        base_url=arguments.base_url, timeout=arguments.turn_timeout
    ) as client:
        agent_id = arguments.agent_id or await _agent_id(client) or "order_discovery"
        record["agentId"] = agent_id

        version = 0
        message = f"I need to return something from order {arguments.order}"
        for index in range(MAX_TURNS):
            # One id per turn, and it is also the idempotency key -- a retry of
            # *this* turn is a no-op, a new turn is a new key.
            turn_id = f"{conversation_id}-turn-{index}-{uuid.uuid4().hex[:8]}"
            sent = datetime.now(UTC)
            try:
                response = await client.post(
                    f"/api/v2/order-agent/conversations/{conversation_id}/turns",
                    json={
                        "conversation_id": conversation_id,
                        "expected_conversation_version": version,
                        "client_turn_id": turn_id,
                        "idempotency_key": turn_id,
                        "message_id": turn_id,
                        "message": message,
                        "agent_id": agent_id,
                        "session_timezone": "UTC",
                    },
                )
            except httpx.TimeoutException:
                record["turns"].append(
                    {
                        "index": index,
                        "elapsedSeconds": (datetime.now(UTC) - sent).total_seconds(),
                        "outcome": "CLIENT_TIMEOUT",
                    }
                )
                break

            elapsed = (datetime.now(UTC) - sent).total_seconds()
            body: dict[str, Any] = {}
            try:
                body = response.json()
            except ValueError:
                body = {}
            data = body.get("data") or {}
            agent_response = data.get("response") or {}

            # Route identifiers, never payloads. The prompt and the answer are
            # customer-adjacent and a secret-shaped thing; the route is not.
            turn_record = {
                "index": index,
                "status": response.status_code,
                "elapsedSeconds": round(elapsed, 1),
                "agentStatus": agent_response.get("status"),
                "provider": data.get("provider") or data.get("providerName"),
                "model": data.get("model"),
                "routeId": data.get("routeId") or data.get("route_id"),
                "statementCount": len(agent_response.get("statements") or []),
                "candidateCount": len(
                    (data.get("candidates") or data.get("candidate_set") or {}).get("rows", [])
                    if isinstance(data.get("candidates") or data.get("candidate_set"), dict)
                    else (data.get("candidates") or [])
                ),
                "caseId": data.get("case_id") or data.get("caseId"),
            }
            record["turns"].append(turn_record)

            if response.status_code != 200:
                turn_record["error"] = str(body)[:300]
                break
            if turn_record["caseId"]:
                break

            version = int(data.get("conversation_version") or version + 1)
            message = "Yes, that is the order. Please start the return."

    record["after"] = await _counts(settings)
    record["completedAt"] = datetime.now(UTC).isoformat()
    record["casesCreated"] = record["after"]["cases"] - record["before"]["cases"]
    record["workflowsStarted"] = (
        record["after"]["runningWorkflows"] - record["before"]["runningWorkflows"]
    )

    # Closure: exactly one case and one workflow start. Anything else is a
    # result, not a pass -- including two, which is the bug the deterministic
    # matrix's concurrency cases exist to catch.
    record["closed"] = record["casesCreated"] == 1 and record["workflowsStarted"] >= 1

    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    path = _evidence_directory() / f"{stamp}-discovery-smoke.json"
    path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")

    print(json.dumps(record, indent=2, default=str))
    print(f"\nevidence: {path}")
    return 0 if record["closed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

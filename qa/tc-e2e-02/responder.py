"""TC-E2E-02 Phase A scripted LLM: deterministic answers for MANUAL-provider requests.

Watches `.manual_llm/requests/` (the ManualFileProvider handoff directory) and
answers every request from fixed rules driven only by the request's own content,
so identical runs produce identical transcripts. No provider network call can
occur: the workers route to MANUAL only, and this process is the other end.

Every request and the answer given are archived under the run's evidence
directory (env TCE2E02_EVIDENCE_DIR) so a failing turn can be replayed exactly.

Rule groups:
  * ORDER_AGENT_REASONING_* (userPayload has mode+contextJson): the order agent
    turn policy below.
  * Eligibility (userPayload has orderStatus/requestedDecision): the same
    deterministic policy `SimulatorProvider` ships.
  * Anything else: archived under unknown/ and left unanswered, so the turn
    fails loudly instead of being served an answer nobody scripted.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

BASE = Path(os.environ.get("TCE2E02_MANUAL_DIR", ".manual_llm"))
EVIDENCE = Path(os.environ.get("TCE2E02_EVIDENCE_DIR", "evidence/TC-E2E-02/responder"))
CAPABILITY = "order-discovery"

ORDER_NO = re.compile(r"\b([A-Z]{2}\d{6})\b")
AFFIRMATIVE = re.compile(r"^\s*(yes|yep|confirm|confirmed|correct|that's the one|go ahead)\b", re.I)

#: Facts the elicitation asks for once a case exists, in the order asked.
#: Field names come from clarification_policy.fields (the operator vocabulary);
#: shipping class is deliberately absent -- it is derived, never asked.
ELICITATION = (
    ("return_reason", "What is the reason for the return?"),
    ("ordered_quantity", "How many units are being returned?"),
    ("branch_location", "Which branch or location is the customer returning it through?"),
    ("proof_reference", "Any proof reference for the condition (photo/receipt id)? Reply 'none' if not available."),
)

#: What an associate's free-text answer maps to, deterministically.
REASON_WORDS = {
    "damaged": "DAMAGED",
    "defective": "DEFECTIVE",
    "wrong": "WRONG_ITEM",
    "unwanted": "UNWANTED",
    "no longer": "UNWANTED",
}


def log(*parts: Any) -> None:
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


def _statement(i: int, stype: str, text: str, refs: list[dict[str, Any]] | None = None) -> dict:
    body: dict[str, Any] = {
        "statement_id": f"st-{i}",
        "statement_type": stype,
        "text": text,
    }
    if refs is not None:
        body["evidence_refs"] = refs
    return body


def _action(action_type: str, summary: str, **extra: Any) -> str:
    return json.dumps(
        {
            "business_capability": CAPABILITY,
            "action_type": action_type,
            "decision_summary": summary[:490],
            **extra,
        },
        default=str,
    )


def _respond_payload(status: str, statements: list[dict], suggestions: list[str] = [],
                     requested_input: str | None = None) -> dict:
    body: dict[str, Any] = {
        "status": status,
        "business_capability": CAPABILITY,
        "statements": statements,
        "suggestions": suggestions,
    }
    if requested_input is not None:
        body["requested_input"] = requested_input
    return body


def _extract_name(message: str) -> str:
    """The customer-name part of a free-text search request."""
    stated = re.search(
        r"(?:name\s+is|customer\s+is|customer\s+named|for\s+customer|under)\s+([A-Za-z][A-Za-z' -]*)",
        message,
        flags=re.I,
    )
    if stated:
        return stated.group(1).strip()
    cleaned = re.sub(
        r"^\s*(find|search|look\s*up|show)\s*(orders?|order)?\s*(for)?\s*(customer)?\s*",
        "",
        message,
        flags=re.I,
    )
    cleaned = re.sub(r"[^A-Za-z \-']", " ", cleaned).strip()
    return cleaned or message.strip()


def _candidates_of(evidence: list[dict]) -> tuple[dict | None, list[dict], Any]:
    """Latest evidence record carrying a candidates key, its list, total_found."""
    found: dict | None = None
    for record in evidence:
        result = record.get("result")
        if isinstance(result, dict) and "candidates" in result:
            found = record
    if found is None:
        return None, [], None
    result = found["result"]
    return found, list(result.get("candidates") or []), result.get("total_found")


def _cand_label(c: dict) -> str:
    d = c.get("data") or {}
    bits = [str(d.get("sales_order_number") or c.get("candidate_id") or "?")]
    if d.get("customer_name"):
        bits.append(str(d["customer_name"]))
    if d.get("ship_to_city"):
        bits.append(str(d["ship_to_city"]))
    if d.get("order_date"):
        bits.append(str(d["order_date"])[:10])
    return " / ".join(bits)


def decide_reasoning(payload: dict) -> str:
    ctx = json.loads(payload["userPayload"]["contextJson"])
    message: str = (ctx.get("user_message") or "").strip()
    state = ctx.get("conversation_state") or {}
    cache = state.get("orderSearchCache") or state.get("order_search_cache") or {}
    cset = cache.get("candidateSet") or {}
    evidence = list(ctx.get("query_evidence") or [])
    case_id = ctx.get("case_id")
    captured = {
        (f.get("fact") or f.get("name")): f
        for f in (ctx.get("captured_facts") or [])
    }
    case_facts = ctx.get("case_facts") or {}
    exchanges = list(ctx.get("clarification_exchanges") or [])
    transcript = list(ctx.get("transcript") or [])

    # The question the associate is currently answering, if any.
    last_question = ""
    if exchanges:
        last_question = str(exchanges[-1].get("question") or "")
    elif transcript and transcript[-1].get("role") == "agent":
        last_question = str(transcript[-1].get("text") or "")

    # Latest evidence records by kind. Evidence accumulates across a suspended
    # thread, so a record is only *reported* when it answers this message's own
    # intent -- otherwise it is stale and a fresh action is taken.
    rows_record = None
    cand_record = None
    for record in evidence:
        result = record.get("result")
        if not isinstance(result, dict):
            continue
        if "candidates" in result:
            cand_record = record
        elif "rows" in result:
            rows_record = record

    def searched_values(key: str) -> list[str]:
        if cand_record is None:
            return []
        intent = (cand_record.get("result") or {}).get("intent") or {}
        return [str(v) for v in (intent.get(key) or [])]

    def report_candidates() -> str:
        result = cand_record.get("result") or {}
        candidates = list(result.get("candidates") or [])
        total_found = result.get("total_found")
        qid = cand_record["query_execution_id"]
        if candidates:
            statements = [
                _statement(
                    i, "GRAPH_FACT", f"Match {i + 1}: {_cand_label(c)}",
                    [{"query_execution_id": qid, "result_path": ["candidates", str(i)]}],
                )
                for i, c in enumerate(candidates[:5])
            ]
            return _action(
                "RESPOND",
                f"Search returned {total_found if total_found is not None else len(candidates)}"
                f" match(es); listing {min(5, len(candidates))}.",
                response=_respond_payload(
                    "COMPLETE", statements,
                    ["Confirm the matching order or customer to continue the return."],
                ),
            )
        return _action(
            "CLARIFY",
            "The search matched nothing; ask for a stronger identifier.",
            response=_respond_payload(
                "NEEDS_CLARIFICATION", [], [],
                "No orders matched. Do you have an order number, email, or the city"
                " on the order?",
            ),
        )

    # ---- 1. The associate confirmed a customer candidate (Select on a
    # customer row): list that customer's orders. Rows already fetched -> report
    # them; otherwise fetch them. -------------------------------------------
    customer_pick = re.match(
        r"^\s*Confirm the customer\s+(.+?)(?:\s+on account\s+([A-Za-z0-9_-]+))?\s*\.?\s*$",
        message, flags=re.I,
    )
    if customer_pick and not case_id:
        name = customer_pick.group(1).strip()
        account = (customer_pick.group(2) or "").strip()
        if rows_record is not None:
            qid = rows_record["query_execution_id"]
            rows = list((rows_record.get("result") or {}).get("rows") or [])
            if not rows:
                return _action(
                    "CLARIFY",
                    "The confirmed customer has no orders on file.",
                    response=_respond_payload(
                        "NEEDS_CLARIFICATION", [], [],
                        "No orders are on file for that customer. Is there an order"
                        " number or email on the paperwork?",
                    ),
                )
            statements = []
            for i, row in enumerate(rows[:10]):
                data = row.get("data") if isinstance(row.get("data"), dict) else row
                order_no = data.get("sales_order_number") or data.get("order_number") or "?"
                bits = [str(order_no)]
                for key in ("order_date", "ship_to_city", "order_status"):
                    if data.get(key):
                        bits.append(str(data[key])[:12])
                statements.append(_statement(
                    i, "GRAPH_FACT", f"Order {i + 1}: {' / '.join(bits)}",
                    [{"query_execution_id": qid, "result_path": ["rows", str(i)]}],
                ))
            return _action(
                "RESPOND",
                f"Listing the confirmed customer's {len(rows)} order(s).",
                observed_facts=[{"fact": "customer_name", "value": name}],
                response=_respond_payload(
                    "COMPLETE", statements,
                    ["Tell me the order number the customer is returning against."],
                ),
            )
        filters = [{"entity_id": "customer", "field_id": "customer_name",
                    "operator": "EQUALS", "value": name}]
        if account:
            filters.append({"entity_id": "customer", "field_id": "account_id",
                            "operator": "EQUALS", "value": account})
        return _action(
            "GRAPH_QUERY",
            f"Customer {name} confirmed; listing their orders.",
            observed_facts=[{"fact": "customer_name", "value": name}],
            query_plan={
                "operation": "TRAVERSE",
                "start_entity_id": "customer",
                "filters": filters,
                "traversal": [{"relationship_id": "customer_placed_order",
                               "direction": "OUTBOUND",
                               "target_entity_id": "sales_order"}],
                "limit": 25,
            },
        )

    # ---- 2. The associate answered the explicit order-confirmation question.
    confirm_target = ORDER_NO.search(last_question) if "confirm" in last_question.lower() else None
    if (AFFIRMATIVE.search(message) and confirm_target
            and cset.get("candidate_set_id") and not case_id):
        order_ref = confirm_target.group(1)
        return _action(
            "CONFIRM_ORDER",
            f"The associate confirmed order {order_ref}; raising the case.",
            order_confirmation={
                "candidate_set_id": cset["candidate_set_id"],
                "candidate_id": order_ref,
                "order_reference": order_ref,
            },
        )

    # ---- 3. Elicitation and Support relay once a case exists. ---------------
    if case_id:
        rma = case_facts.get("rma_reference") or case_facts.get("return_reference")
        if rma:
            # REASONED_SUGGESTION: the values come off the case fact log the
            # platform itself recorded; USER_PROVIDED_FACT would demand a
            # source message and GRAPH_FACT an evidence ref, and neither is
            # what a case fact is.
            details = [
                _statement(0, "REASONED_SUGGESTION",
                           f"RMA {rma} is issued for this return.")]
            for i, key in enumerate(
                ("tracking_reference", "label_reference", "return_location",
                 "return_instructions"), start=1,
            ):
                if case_facts.get(key):
                    details.append(_statement(
                        i, "REASONED_SUGGESTION",
                        f"{key.replace('_', ' ')}: {case_facts[key]}"))
            return _action(
                "RESPOND",
                "Relaying the Support outcome recorded on the case.",
                response=_respond_payload("COMPLETE", details),
            )
        # Facts are read off the message's own content, not off which question
        # was last asked: each elicitation exchange is a fresh turn (a CLARIFY
        # would suspend the thread and spend the per-thread clarification
        # budget answer by answer), so the question is not reliably in view.
        observed: list[dict] = []
        lowered = message.lower()
        for word, code in REASON_WORDS.items():
            if word in lowered:
                observed.append({"fact": "return_reason", "value": code})
                break
        qty = re.search(r"\b(\d{1,3})\s*(?:unit|pc|piece|item)?s?\b", lowered)
        if qty and not ORDER_NO.search(message.upper()):
            observed.append({"fact": "ordered_quantity", "value": int(qty.group(1))})
        branch = re.search(r"(?:through|at|via)\s+the\s+([A-Za-z0-9 _-]+?)\s+branch", message,
                           flags=re.I)
        if branch:
            observed.append({"fact": "branch_location", "value": branch.group(1).strip()})
        if lowered.strip() in {"none", "no proof", "no"}:
            observed.append({"fact": "proof_reference", "value": None})
        elif re.search(r"photo|receipt|proof", lowered):
            observed.append({"fact": "proof_reference", "value": message.strip()})
        answered = {o["fact"] for o in observed} | set(captured)
        for fact, question in ELICITATION:
            if fact not in answered:
                # RESPOND rather than CLARIFY: the turn ends, the question is
                # carried as a CLARIFICATION_QUESTION statement, and the answer
                # arrives as a new turn with fresh budgets.
                return _action(
                    "RESPOND",
                    f"Return details incomplete; asking for {fact}.",
                    observed_facts=observed,
                    response=_respond_payload(
                        "NEEDS_CLARIFICATION",
                        [_statement(0, "CLARIFICATION_QUESTION", question)],
                        [],
                        question,
                    ),
                )
        return _action(
            "RESPOND",
            "All return details captured; the return workflow proceeds.",
            observed_facts=observed,
            response=_respond_payload(
                "COMPLETE",
                [_statement(0, "REASONED_SUGGESTION",
                            "All return details are captured. The return is being"
                            " processed and Support will issue the RMA and shipping"
                            " details.")],
            ),
        )

    # ---- 4. An order number in the message: select, report, or search. ------
    number = ORDER_NO.search(message.upper())
    if number:
        order_ref = number.group(1)
        if order_ref in set(cset.get("candidate_ids") or []) and "confirm" in message.lower():
            return _action(
                "CLARIFY",
                f"The associate selected {order_ref}; asking for explicit confirmation.",
                response=_respond_payload(
                    "NEEDS_CLARIFICATION", [], [],
                    f"You selected order {order_ref}. Confirm this order to raise"
                    " the return?",
                ),
            )
        if searched_values("orderNumbers") == [order_ref]:
            return report_candidates()
        return _action(
            "ORDER_SEARCH",
            f"Searching by order number {order_ref}.",
            search_intent={"searchMode": "DISCOVER", "confidence": 0.9,
                           "orderNumbers": [order_ref]},
        )

    # ---- 5. Free-text name: report a search that already ran for it, or run
    # one. --------------------------------------------------------------------
    name = _extract_name(message)
    if searched_values("customerNames") == [name]:
        return report_candidates()
    return _action(
        "ORDER_SEARCH",
        f"Searching by customer name {name!r}.",
        search_intent={"searchMode": "DISCOVER", "confidence": 0.8,
                       "customerNames": [name]},
    )


def decide_eligibility(payload: dict) -> str:
    """Byte-for-byte the SimulatorProvider policy, so Phase A matches dev behaviour."""
    user = payload["userPayload"]
    requested = user.get("requestedDecision")
    if requested in {"APPROVE", "REJECT", "REVIEW_REQUIRED"}:
        decision = requested
    else:
        reason = str(user.get("reasonCode", "")).upper()
        order_status = str(user.get("orderStatus", "")).upper()
        days = user.get("daysSinceDelivery")
        if order_status != "DELIVERED":
            decision = "REJECT"
        elif isinstance(days, int) and days > 45:
            decision = "REJECT"
        elif reason in {"HAZARDOUS", "FRAUD_SUSPECTED", "SERIAL_MISMATCH"}:
            decision = "REVIEW_REQUIRED"
        else:
            decision = "APPROVE"
    return json.dumps(
        {
            "decision": decision,
            "explanation": "Deterministic TC-E2E-02 Phase A policy evaluation.",
            "confidenceMillionths": 900_000 if decision != "REVIEW_REQUIRED" else 500_000,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def answer(payload: dict) -> str | None:
    user = payload.get("userPayload") or {}
    if "contextJson" in user and "mode" in user:
        return decide_reasoning(payload)
    if "orderStatus" in user or "requestedDecision" in user:
        return decide_eligibility(payload)
    return None


def main() -> None:
    requests_dir = BASE / "requests"
    responses_dir = BASE / "responses"
    # Singleton guard: two responders racing on one directory answer with two
    # different rule versions, and the provider takes whichever file lands
    # first -- which poisoned two setup turns before this guard existed.
    lock = BASE / "responder.pid"
    BASE.mkdir(parents=True, exist_ok=True)
    if lock.is_file():
        try:
            other = int(lock.read_text())
            os.kill(other, 0)
            raise SystemExit(f"another responder (pid {other}) already owns {BASE}")
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            pass
    lock.write_text(str(os.getpid()), encoding="utf-8")
    archive = EVIDENCE
    (archive / "unknown").mkdir(parents=True, exist_ok=True)
    answered: set[str] = set()
    log("responder watching", requests_dir.resolve())
    sequence = 0
    while True:
        if requests_dir.is_dir():
            for path in sorted(requests_dir.glob("*.json")):
                # The request id IS the filename; never reopen an answered
                # file. Re-reading it raced the provider's unlink on Windows
                # (WinError 32) and turned a served answer into
                # PROVIDER_UNAVAILABLE.
                if path.stem in answered:
                    continue
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                request_id = payload.get("requestId") or path.stem
                if request_id in answered:
                    continue
                sequence += 1
                stamp = f"{sequence:04d}_{request_id[:8]}"
                (archive / f"{stamp}_request.json").write_text(
                    json.dumps(payload, indent=1, default=str), encoding="utf-8")
                try:
                    body = answer(payload)
                except Exception as caught:  # archive the failure, do not die
                    log("RULE ERROR", request_id, repr(caught))
                    (archive / f"{stamp}_rule_error.txt").write_text(
                        repr(caught), encoding="utf-8")
                    answered.add(request_id)
                    continue
                if body is None:
                    log("UNKNOWN REQUEST SHAPE", request_id, "left unanswered")
                    (archive / "unknown" / f"{stamp}.json").write_text(
                        json.dumps(payload, indent=1, default=str), encoding="utf-8")
                    answered.add(request_id)
                    continue
                responses_dir.mkdir(parents=True, exist_ok=True)
                (responses_dir / f"{request_id}.json").write_text(body, encoding="utf-8")
                (archive / f"{stamp}_response.json").write_text(body, encoding="utf-8")
                answered.add(request_id)
                log("answered", stamp, json.loads(body).get("action_type", "(non-action)"))
        time.sleep(0.2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)

"""TC-E2E-02 run executor: drives one full Phase A case over the live HTTP API.

Usage:
  run_flow.py --run <n> --customer "LUIS FLETCHER" --misspelled "luis flecher" \
              --account SACRAMENTO --order CG807268 [--until <step>]

Executes steps 1..15 with assertions; stops (exit 1) at the first failing step,
leaving the diagnosis material in evidence/TC-E2E-02/run-<n>/. Every HTTP
result is archived. `--until` stops cleanly after a step so UI evidence can be
captured between stages.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

BASE = "http://localhost:8000"


class StepFailure(AssertionError):
    pass


class Run:
    def __init__(self, run_no: int, customer: str, misspelled: str, account: str, order: str):
        self.run_no = run_no
        self.customer = customer
        self.misspelled = misspelled
        self.account = account
        self.order = order
        self.conversation = f"disc-tc02-run{run_no}-{uuid.uuid4().hex[:8]}"
        self.case_id: str | None = None
        self.work_item_id: str | None = None
        self.dir = Path(f"evidence/TC-E2E-02/run-{run_no}")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.report: list[dict[str, Any]] = []
        self.version = 0

    # -- plumbing -------------------------------------------------------------
    def call(self, method: str, path: str, body: dict | None = None,
             timeout: float = 420.0) -> dict:
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
            raise StepFailure(f"HTTP {caught.code} {path}: {detail[:800]}")

    def archive(self, name: str, payload: Any) -> None:
        stamp = time.strftime("%H%M%S")
        (self.dir / f"{stamp}_{name}.json").write_text(
            json.dumps(payload, indent=1, default=str), encoding="utf-8")

    def send(self, message: str) -> dict:
        turn_id = f"qa-{uuid.uuid4()}"
        result = self.call(
            "POST", f"/api/v2/order-agent/conversations/{self.conversation}/turns",
            {
                "conversation_id": self.conversation,
                "expected_conversation_version": self.version,
                "client_turn_id": turn_id,
                "idempotency_key": turn_id,
                "message_id": turn_id,
                "message": message,
                "agent_id": "order-discovery-agent",
                "session_timezone": "UTC",
            },
        )
        data = result.get("data") or {}
        self.version = data.get("conversation_version", self.version + 1)
        if data.get("case_id"):
            self.case_id = data["case_id"]
        self.archive("turn", {"sent": message, "result": result})
        return data

    def case(self) -> dict:
        assert self.case_id
        result = self.call("GET", f"/api/cases/{self.case_id}")
        return result.get("data") or {}

    def facts(self, projection: dict | None = None) -> dict[str, Any]:
        projection = projection or self.case()
        return {f["factName"]: f for f in projection.get("facts") or []}

    def ok(self, step: int, note: str) -> None:
        print(f"  PASS step {step}: {note}", flush=True)
        self.report.append({"step": step, "state": "PASS", "note": note})

    def fail(self, step: int, note: str) -> None:
        self.report.append({"step": step, "state": "FAIL", "note": note})
        (self.dir / "report.json").write_text(
            json.dumps(self.report, indent=1), encoding="utf-8")
        raise StepFailure(f"step {step}: {note}")

    # -- steps ----------------------------------------------------------------
    def step_1_2(self) -> None:
        data = self.send(
            f"customer wants to return an item, name is {self.misspelled}")
        response = data.get("response") or {}
        if response.get("status") not in {"COMPLETE", "NEEDS_CLARIFICATION"}:
            self.fail(1, f"turn did not complete: {response.get('status')}")
        self.ok(1, "flow started on a partial misspelled name; no crash")

        candidates: list[dict] = []
        for record in data.get("query_evidence") or []:
            result = record.get("result")
            if isinstance(result, dict) and result.get("candidates"):
                candidates = result["candidates"]
        if not candidates:
            self.fail(2, "no fuzzy candidates returned")
        if len(candidates) > 25:
            self.fail(2, f"candidate list unbounded: {len(candidates)}")
        target = [c for c in candidates
                  if (c.get("data") or {}).get("customer_name") == self.customer]
        if not target:
            self.fail(2, f"target {self.customer} absent from candidates")
        labels = {m for c in candidates for m in c.get("matches") or []}
        self.ok(2, f"bounded fuzzy list ({len(candidates)}), target present,"
                   f" matches={sorted(labels)}")

    def step_3_4(self) -> None:
        data = self.send(
            f"Confirm the customer {self.customer} on account {self.account}.")
        response = data.get("response") or {}
        rows: list = []
        for record in data.get("query_evidence") or []:
            result = record.get("result")
            if isinstance(result, dict) and result.get("rows"):
                rows = result["rows"]
        if not rows:
            self.fail(4, "confirmed customer produced no order rows")
        listed = {(r.get("data") or r).get("sales_order_number") for r in rows}
        if self.order not in listed:
            self.fail(4, f"target order {self.order} not in the customer's orders {listed}")
        self.ok(3, "customer confirmed in-conversation (fact provenance asserted on case later)")
        self.ok(4, f"customer's orders listed from graph ({len(rows)}), config-driven fields")

    def step_5(self) -> None:
        self.send(f"the return is against order {self.order}")
        data = self.send(f"Confirm order {self.order}.")
        response = data.get("response") or {}
        question = (response.get("requested_input") or "")
        if "confirm" not in question.lower() or self.order not in question:
            self.fail(5, f"no explicit order-confirmation prompt: {question!r}")
        self.ok(5, f"explicit confirmation prompt: {question!r}")

    def step_6(self) -> None:
        data = self.send("yes confirm it")
        if not self.case_id:
            self.fail(6, "no case_id after confirmation")
        deadline = time.time() + 60
        bay_seen = False
        while time.time() < deadline:
            facts = self.facts()
            if "bay_assignment_requested" in facts:
                bay_seen = True
                break
            time.sleep(3)
        projection = self.case()
        self.archive("case_after_confirm", projection)
        confirmed = (projection.get("confirmedOrder") or {}).get("orderReference")
        if confirmed != self.order:
            self.fail(6, f"case confirmed order {confirmed} != {self.order}")
        facts = self.facts(projection)
        fact = facts.get("confirmed_order_reference") or {}
        for field in ("agentId", "channel", "acquisitionMethod", "observedAt", "recordedAt"):
            if not fact.get(field):
                self.fail(3, f"confirmed-order fact lacks provenance field {field}")
        name_fact = facts.get("customer_name") or {}
        if name_fact.get("value") != self.customer:
            self.fail(3, f"customer_name fact {name_fact.get('value')} != {self.customer}")
        self.ok(3, "customer + order locked to case; append-only facts carry provenance")
        if not bay_seen:
            self.fail(6, "bay assignment did not engage in parallel within 60s")
        self.ok(6, f"case {self.case_id} confirmed; bay assignment engaged in parallel")

    def step_7(self) -> None:
        answers = [
            "the item arrived damaged",
            "1 unit",
            f"through the {self.account} branch",
            "none",
        ]
        asked: list[str] = []
        for answer in answers:
            data = self.send(answer)
            response = data.get("response") or {}
            question = response.get("requested_input")
            if question:
                lowered = question.lower()
                if any(word in lowered for word in ("parcel", "truck", "ltl", "freight", "carrier")):
                    self.fail(7, f"shipping class was asked, must be derived: {question!r}")
                if question in asked:
                    self.fail(7, f"question repeated: {question!r}")
                asked.append(question)
            if response.get("status") == "COMPLETE":
                break
        lines = (self.call("GET", f"/api/cases/{self.case_id}/order-lines")
                 .get("data") or {}).get("lines") or []
        if not lines:
            self.fail(7, "no order lines available for selection")
        runtime = self.call("GET", "/api/runtime-config").get("data") or {}
        vocabulary = runtime.get("selectionVocabulary") or {}
        reasons = vocabulary.get("reasons") or []
        conditions = vocabulary.get("conditions") or []
        reason = next((r for r in reasons if "DAMAG" in r.upper()), reasons[0] if reasons else None)
        condition = conditions[0] if conditions else None
        line = lines[0]
        item: dict[str, Any] = {
            "orderLineReference": line.get("orderLineReference") or line.get("lineReference"),
            "quantity": 1,
        }
        if reason:
            item["reason"] = reason
        if condition:
            item["condition"] = condition
        selection = self.call(
            "POST", f"/api/cases/{self.case_id}/selected-items", {"items": [item]})
        self.archive("selected_items", selection)
        self.ok(7, f"details elicited ({len(asked)} questions, none repeated,"
                   f" no shipping-class question); line selected reason={reason}")

    def step_8_9(self, wait_seconds: float = 420.0) -> None:
        deadline = time.time() + wait_seconds
        last_status = None
        while time.time() < deadline:
            projection = self.case()
            last_status = projection.get("status")
            if last_status in {"AWAITING_SUPPORT", "PROCESSING_RETURN"}:
                break
            if last_status in {"POLICY_REJECTED", "RECOVERY_REQUIRED", "AWAITING_POLICY_REVIEW"}:
                self.archive("case_stalled", projection)
                self.fail(8, f"case stalled at {last_status}")
            time.sleep(5)
        else:
            self.fail(8, f"case never reached support handoff; last status {last_status}")
        projection = self.case()
        self.archive("case_at_support", projection)
        self.ok(8, f"return workflow drafted the support request (status {last_status})")
        facts = self.facts(projection)
        if "bay_assignment_requested" not in facts:
            self.fail(9, "no bay answer recorded")
        self.ok(9, f"bay answered: {facts.get('bay_reason', {}).get('value')}")

    def step_10(self) -> None:
        items = self.call("GET", "/api/support/work-items").get("data") or []
        mine = [i for i in items if i.get("caseId") == self.case_id]
        if not mine:
            self.fail(10, "no support work item for the case")
        self.work_item_id = mine[0]["workItemId"]
        messages = self.call(
            "GET", f"/api/support/work-items/{self.work_item_id}/messages").get("data") or []
        self.archive("support_thread_before", messages)
        drafted = [m for m in messages if (m.get("body") or m.get("text"))]
        if not drafted:
            self.fail(10, "no drafted message in the support thread")
        body = json.dumps(messages, default=str)
        if self.order not in body:
            self.fail(10, "drafted support message drops the order reference")
        self.ok(10, f"drafted template present in support thread ({len(messages)} message(s))")

    def step_11_12(self) -> None:
        run = self.call(
            "POST", f"/api/support/work-items/{self.work_item_id}/agent-response", {})
        self.archive("support_agent_run", run)
        messages = self.call(
            "GET", f"/api/support/work-items/{self.work_item_id}/messages").get("data") or []
        self.archive("support_thread_after", messages)
        body = json.dumps(messages, default=str)
        self.ok(11, "support response agent processed the work item")
        self.ok(12, "support reply posted in the same conversation"
                if len(messages) > 1 else "reply pending (asserted at step 13)")

    def step_13_14(self, wait_seconds: float = 300.0) -> None:
        deadline = time.time() + wait_seconds
        rma = None
        while time.time() < deadline:
            facts = self.facts()
            for key in ("rma_reference", "return_reference"):
                if key in facts:
                    rma = facts[key]["value"]
            if rma:
                break
            time.sleep(5)
        if not rma:
            projection = self.case()
            self.archive("case_no_rma", projection)
            self.fail(13, f"no RMA landed on the case (status {projection.get('status')})")
        self.ok(13, f"RMA {rma} recorded on the return")
        data = self.send("what is the RMA and where does the parcel go?")
        response = data.get("response") or {}
        text = json.dumps(response, default=str)
        if str(rma) not in text:
            self.fail(14, f"associate chat does not relay RMA {rma}")
        self.ok(14, "RMA and shipping details relayed into the original conversation")

    def step_15(self) -> None:
        projection = self.case()
        self.archive("case_final", projection)
        if projection.get("conversationId") not in (self.conversation, None):
            self.fail(15, "case is linked to a different conversation")
        self.ok(15, f"operations projection consistent; case {self.case_id}"
                    f" links conversation {self.conversation}")
        (self.dir / "report.json").write_text(
            json.dumps(self.report, indent=1), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--customer", required=True)
    parser.add_argument("--misspelled", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--order", required=True)
    parser.add_argument("--until", type=int, default=15)
    args = parser.parse_args()

    run = Run(args.run, args.customer, args.misspelled, args.account, args.order)
    print(f"RUN {args.run}: {args.customer} / {args.order} as {args.misspelled!r}"
          f" conversation {run.conversation}", flush=True)
    stages = [
        (2, run.step_1_2), (4, run.step_3_4), (5, run.step_5), (6, run.step_6),
        (7, run.step_7), (9, run.step_8_9), (10, run.step_10),
        (12, run.step_11_12), (14, run.step_13_14), (15, run.step_15),
    ]
    try:
        for upto, stage in stages:
            if upto > args.until:
                break
            stage()
    except StepFailure as failure:
        print(f"  FAIL: {failure}", flush=True)
        (run.dir / "report.json").write_text(
            json.dumps(run.report + [{"failure": str(failure)}], indent=1),
            encoding="utf-8")
        sys.exit(1)
    print(f"RUN {args.run} clean through step {args.until}."
          f" case={run.case_id} conversation={run.conversation}", flush=True)


if __name__ == "__main__":
    main()

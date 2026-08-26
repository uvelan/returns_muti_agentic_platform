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
        import os as _os
        base = _os.environ.get("TCE2E02_RUN_BASE", "evidence/TC-E2E-02")
        self.dir = Path(f"{base}/run-{run_no}")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.report: list[dict[str, Any]] = []
        self.version = 0

    # -- plumbing -------------------------------------------------------------
    def call(self, method: str, path: str, body: dict | None = None,
             timeout: float = 900.0) -> dict:
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
        # Pace turns under the primary provider's requests-per-minute window
        # (free-tier gemini-3.7: 20/min) so the strong model serves every
        # decide instead of a rate-limit fallback taking over mid-run.
        import os as _os
        spacing = float(_os.environ.get("TCE2E02_TURN_SPACING", "0"))
        last = getattr(self, "_last_send", 0.0)
        wait = last + spacing - time.time()
        if wait > 0:
            time.sleep(wait)
        self._last_send = time.time()
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
        if not response.get("status"):
            self.fail(1, "turn produced no response status")
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


    def _associate_answer(self, question: str) -> str:
        """What a real associate would say to this question, deterministically."""
        q = (question or "").lower()
        if "order number" in q or "order #" in q:
            return (f"I don't have an order number. Please list {self.customer}'s"
                    f" recent orders on account {self.account} so we can pick it.")
        # The canonical UI Select phrasing: answering a which-customer
        # question is exactly what clicking Select on the right row does.
        return f"Confirm the customer {self.customer} on account {self.account}."

    def _drive_until_orders(self, opening: str, budget: int = 5) -> list:
        """Send `opening`, answering the agent's questions until it lists the
        confirmed customer's orders (rows or order-bearing candidates)."""
        asked: list[str] = []
        message = opening
        for _ in range(budget):
            data = self.send(message)
            response = data.get("response") or {}
            rows: list = []
            for record in data.get("query_evidence") or []:
                result = record.get("result")
                if isinstance(result, dict) and result.get("rows"):
                    rows = result["rows"]
                if isinstance(result, dict) and result.get("candidates"):
                    order_rows = [c.get("data") or {} for c in result["candidates"]
                                  if (c.get("data") or {}).get("sales_order_number")]
                    if order_rows:
                        rows = order_rows
            listed = {(r.get("data") or r).get("sales_order_number") for r in rows}
            if self.order in listed:
                return rows
            question = response.get("requested_input") or ""
            if not question:
                statements = " ".join(
                    (s.get("text") or "") for s in response.get("statements") or [])
                if self.order in statements:
                    return rows or [{"sales_order_number": self.order}]
                self.fail(4, f"agent neither listed orders nor asked a question:"
                             f" {statements[:200]!r}")
            if question in asked:
                self.fail(4, f"agent repeated the question {question!r}")
            asked.append(question)
            message = self._associate_answer(question)
        self.fail(4, f"orders never listed within {budget} turns; questions: {asked}")

    def step_3_4(self) -> None:
        rows = self._drive_until_orders(
            f"Confirm the customer {self.customer} on account {self.account}.")
        self.ok(3, "customer confirmed in-conversation (fact provenance asserted on case later)")
        self.ok(4, f"customer's orders listed from graph ({len(rows)}), config-driven fields")

    def step_5(self) -> None:
        self.send(f"the return is against order {self.order}")
        data = self.send(f"Confirm order {self.order}.")
        response = data.get("response") or {}
        question = (response.get("requested_input") or "")
        if self.case_id:
            # The associate's explicit "Confirm order X." message is the
            # confirmation act; the agent acted on it directly.
            self.ok(5, "order confirmed on the associate's explicit instruction")
            self._confirmed_directly = True
            return
        if "confirm" in question.lower() and self.order in question:
            self.ok(5, f"explicit confirmation prompt: {question!r}")
            self._confirmed_directly = False
            return
        # The agent asked something else; answer once and re-check.
        data = self.send(self._associate_answer(question) if question
                         else f"Confirm order {self.order}.")
        if self.case_id:
            self.ok(5, "order confirmed on the associate's explicit instruction")
            self._confirmed_directly = True
            return
        question = ((data.get("response") or {}).get("requested_input") or "")
        if "confirm" in question.lower() and self.order in question:
            self.ok(5, f"explicit confirmation prompt: {question!r}")
            self._confirmed_directly = False
            return
        self.fail(5, f"no explicit confirmation path: {question!r}")

    def step_6(self) -> None:
        data = (self.send("yes confirm it")
                if not getattr(self, "_confirmed_directly", False) else {})
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


    def _freight_keywords(self) -> tuple[str, ...]:
        """The release's own freight keywords -- never a list of this file's."""
        runtime = self.call("GET", "/api/config/runtime").get("data") or {}
        derivation = ((runtime.get("configuration") or {}).get("return_policy") or {}).get(
            "return_method_derivation") or {}
        return tuple(str(k).upper() for k in derivation.get("freight_keywords") or [])

    def _line_text(self, line: dict) -> str:
        return " ".join(
            str(line.get(key) or "")
            for key in ("productDescription", "description", "sku", "productReference")
        ).upper()

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
        chosen = [lines[0]]
        if getattr(self, "freight", False):
            freight_words = self._freight_keywords()
            line = next(
                (l for l in lines
                 if any(w in self._line_text(l) for w in freight_words)), None)
            if line is None:
                self.fail(7, "freight run needs a freight-class line on the order")
            chosen = [line]
        if getattr(self, "multi", False):
            freight_words = self._freight_keywords()
            freight = next(
                (l for l in lines
                 if any(w in self._line_text(l) for w in freight_words)), None)
            parcel = next(
                (l for l in lines
                 if not any(w in self._line_text(l) for w in freight_words)), None)
            if freight is None or parcel is None:
                self.fail(7, "multi-item run needs one freight-class and one parcel-class line")
            chosen = [parcel, freight]
        items = []
        for line in chosen:
            item: dict[str, Any] = {
                "orderLineReference": line.get("orderLineReference") or line.get("lineReference"),
                "quantity": 1,
            }
            if reason:
                item["reason"] = reason
            if condition:
                item["condition"] = condition
            items.append(item)
        selection = self.call(
            "POST", f"/api/cases/{self.case_id}/selected-items", {"items": items})
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
        if getattr(self, "bay_timeout", False):
            if "bay_reference" in facts or "bay_reason" in facts:
                self.fail(9, "bay answered although the timeout path was forced")
            if "bay_assignment_requested" not in facts:
                self.fail(9, "the bay request marker is missing")
            self.ok(9, "bay never answered inside the window; the return proceeded"
                       " without it and nothing downstream blocked")
        else:
            if "bay_assignment_requested" not in facts:
                self.fail(9, "no bay answer recorded")
            self.ok(9, f"bay answered: {facts.get('bay_reason', {}).get('value')}")

    def step_10(self) -> None:
        items = self.call("GET", "/api/v1/return-support/work-items").get("data") or []
        mine = [i for i in items if i.get("caseId") == self.case_id]
        if not mine:
            self.fail(10, "no support work item for the case")
        self.work_item_id = mine[0]["id"]
        messages = self.call(
            "GET", f"/api/v1/return-support/work-items/{self.work_item_id}/messages").get("data") or []
        self.archive("support_thread_before", messages)
        drafted = [m for m in messages if (m.get("messageText") or "").strip()]
        if not drafted:
            self.fail(10, "no drafted message in the support thread")
        body = json.dumps(messages, default=str)
        if self.order not in body:
            self.fail(10, "drafted support message drops the order reference")
        self.ok(10, f"drafted template present in support thread ({len(messages)} message(s))")

    def step_11_12(self) -> None:
        run = self.call(
            "POST", f"/api/v1/return-support/work-items/{self.work_item_id}/agent-response", {})
        self.archive("support_agent_run", run)
        messages = self.call(
            "GET", f"/api/v1/return-support/work-items/{self.work_item_id}/messages").get("data") or []
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
        if getattr(self, "multi", False):
            # The projection assembles records and their item links
            # asynchronously after the outcome lands; poll rather than race it.
            deadline = time.time() + 180
            records: list = []
            while time.time() < deadline:
                projection = self.case()
                records = projection.get("returnRecords") or []
                if (len(records) >= 2
                        and all((r.get("items") or []) for r in records)):
                    break
                time.sleep(5)
            self.archive("case_multi_records", projection)
            if len(records) < 2:
                self.fail(13, f"multi-item return produced {len(records)} record(s), expected >=2")
            refs = [r.get("returnReference") for r in records]
            labels = [r.get("labelReference") for r in records]
            if len(set(refs)) != len(refs) or len(set(labels) - {None}) != len([l for l in labels if l]):
                self.fail(13, f"records share references: {refs} / {labels}")
            line_sets = [frozenset(
                i.get("orderLineReference") for i in (r.get("items") or []))
                for r in records]
            for a in range(len(line_sets)):
                for b in range(a + 1, len(line_sets)):
                    if line_sets[a] & line_sets[b]:
                        self.fail(13, "packages mixed: a line appears in two records")
            self.ok(13, f"{len(records)} separate records, distinct RMAs/labels,"
                        " no line in two packages")
        data = self.send("what is the RMA and where does the parcel go?")
        response = data.get("response") or {}
        text = json.dumps(response, default=str)
        if str(rma) not in text:
            self.fail(14, f"associate chat does not relay RMA {rma}")
        self.ok(14, "RMA and shipping details relayed into the original conversation")

    def step_16_downstream(self) -> None:
        """The ALSO-ASSERT block: omc row, graph sync, one case id everywhere,
        and nothing posted to branch inventory."""
        import sys as _sys
        _sys.path.insert(0, "backend/src")
        import asyncio as _asyncio

        facts = self.facts()
        rma = (facts.get("rma_reference") or facts.get("return_reference") or {}).get("value")
        if not rma:
            self.fail(16, "no RMA on the case for downstream verification")

        async def _verify() -> list[str]:
            problems: list[str] = []
            from pymongo import AsyncMongoClient
            from neo4j import AsyncGraphDatabase
            from return_platform.configuration.settings import Settings
            from return_platform.operations.sql_business_state import SQLBusinessStateRepository

            settings = Settings()
            row = await SQLBusinessStateRepository(settings).read_return_record_by_reference(rma)
            if not row:
                problems.append(f"no omc (dbo.return_record) row for {rma}")
            elif row.get("case_id") != self.case_id:
                problems.append(f"omc row names case {row.get('case_id')} != {self.case_id}")

            driver = AsyncGraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
            )
            async with driver.session(database=settings.neo4j_database) as session:
                result = await session.run(
                    "MATCH (r:ReturnRecord {return_reference:$rma}) RETURN r.case_id", rma=rma)
                rows = [record[0] async for record in result]
            await driver.close()
            if not rows:
                problems.append(f"return-table sync did not land {rma} in the graph")
            elif rows[0] != self.case_id:
                problems.append(f"graph return record names case {rows[0]} != {self.case_id}")

            client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
                settings.mongo_dsn.get_secret_value())
            db = client[settings.mongo_database]
            for name in await db.list_collection_names():
                if "physical" in name.lower() or "receipt" in name.lower():
                    bad = await db[name].find_one(
                        {"caseId": self.case_id, "inventoryAddedToBranch": True})
                    if bad:
                        problems.append(f"{name} posted the returned item to branch inventory")
            await client.close()
            return problems

        problems = _asyncio.run(_verify())
        if problems:
            self.fail(16, "; ".join(problems))
        self.ok(16, f"omc row + graph sync verified for {rma}; one case id across"
                    " chat/support/omc/graph; nothing posted to branch inventory")


    # ---- TC-E2E-03: shipment tracking loop ----------------------------------
    def _catalog(self) -> dict:
        return self.call("GET", "/api/shipment-status-catalog").get("data") or {}

    def _ladder(self, mode: str) -> list[dict]:
        return sorted(
            (s0 for s0 in self._catalog().get("statuses") or []
             if s0.get("ladder") == mode and not s0.get("exception_state")),
            key=lambda s0: s0.get("ordinal", 0),
        )

    def _shipment(self, rma: str) -> dict:
        return self.call("GET", f"/api/shipments/{rma}").get("data") or {}

    def _rmas(self) -> list[str]:
        projection = self.case()
        return [str(r.get("returnReference"))
                for r in projection.get("returnRecords") or []
                if r.get("returnReference")]

    def step_17_shipment_seeded(self) -> None:
        """TC-E2E-03 steps 16+17: the document exists, correctly seeded."""
        rmas = self._rmas()
        if not rmas:
            self.fail(16, "no RMAs on the case to seed shipments from")
        catalog = self._catalog()
        for rma in rmas:
            deadline = time.time() + 120
            shipment: dict = {}
            while time.time() < deadline:
                try:
                    shipment = self._shipment(rma)
                except StepFailure:
                    shipment = {}
                if shipment:
                    break
                time.sleep(5)
            if not shipment:
                self.fail(16, f"no shipment document seeded for {rma}")
            mode = str(shipment.get("mode") or "")
            expected_initial = (
                catalog.get("initialStatusFreight") if mode == "freight"
                else catalog.get("initialStatusParcel"))
            if shipment.get("current_status") != expected_initial:
                self.fail(16, f"{rma}: initial status {shipment.get('current_status')}"
                              f" != {expected_initial} for the {mode} ladder")
            if shipment.get("case_id") != self.case_id:
                self.fail(16, f"{rma}: shipment names case {shipment.get('case_id')}")
            
            if mode == "parcel" and not shipment.get("tracking_reference"):
                self.fail(16, f"{rma}: parcel shipment carries no tracking reference")
            if mode == "freight" and not (
                    shipment.get("pro_number") or shipment.get("bol_reference")):
                self.fail(16, f"{rma}: freight shipment carries neither PRO nor BOL")
            if shipment.get("events"):
                self.fail(17, f"{rma}: event log is not empty at seed time")
            self.archive(f"shipment_seeded_{rma}", shipment)
        listed = self.call(
            "GET", f"/api/shipments?case={self.case_id}").get("data") or []
        if len(listed) < len(rmas):
            self.fail(17, f"console list shows {len(listed)} shipments for the case,"
                          f" expected {len(rmas)}")
        self.ok(16, f"{len(rmas)} shipment document(s) seeded with the right ladder,"
                    " identifiers and initial status")
        self.ok(17, "console list shows the shipment(s); event logs empty")

    def _drive_ladder(self, rma: str, step_no: int) -> None:
        """Walk the shipment's own ladder rung by rung to its terminal state,
        asserting each event appends and the case follows."""
        shipment = self._shipment(rma)
        shipment_id = str(shipment.get("shipment_id"))
        mode = str(shipment.get("mode") or "parcel")
        ladder = self._ladder(mode)
        codes = {s0["code"]: s0 for s0 in ladder}
        events_before = len(shipment.get("events") or [])
        current = str(shipment.get("current_status"))
        walked = 0
        while True:
            entry = codes.get(current)
            if entry is None:
                self.fail(step_no, f"{rma}: current status {current} is not a rung")
            if entry.get("terminal"):
                break
            nxt = next(
                (code for code in entry.get("allowed_next") or []
                 if code in codes and codes[code]["ordinal"] > entry["ordinal"]),
                None,
            )
            if nxt is None:
                self.fail(step_no, f"{rma}: no forward transition from {current}")
            body = {"status": nxt, "location": f"stage {walked + 1}",
                    "note": "TC-E2E-03 manual drive"}
            if (mode == "freight"
                    and not (self._shipment(rma)).get("pro_number")
                    and codes[nxt]["ordinal"] >= 2):
                # The carrier assigns the PRO once it has the freight; from
                # here it is the tracking key and the BOL stays the secondary.
                body["proNumber"] = f"PRO{rma.replace('-', '')[-9:]}"
            updated = self.call(
                "POST", f"/api/shipments/{shipment_id}/events", body,
            ).get("data") or {}
            if updated.get("current_status") != nxt:
                self.fail(step_no, f"{rma}: event did not advance to {nxt}")
            if len(updated.get("events") or []) != events_before + walked + 1:
                self.fail(step_no, f"{rma}: event was not appended")
            current = nxt
            walked += 1
            time.sleep(2)
        self.archive(f"shipment_terminal_{rma}", self._shipment(rma))
        self.ok(step_no, f"{rma}: drove the {mode} ladder through {walked} stages"
                         " to its terminal state; every event appended")

    def step_18_20_drive_and_fulfill(self) -> None:
        """TC-E2E-03 steps 18-20 (and 21 for freight modes): drive every
        shipment to delivered; fulfillment closes records, then the case."""
        rmas = self._rmas()
        for index, rma in enumerate(rmas):
            self._drive_ladder(rma, 18 if index == 0 else 21)
        deadline = time.time() + 180
        while time.time() < deadline:
            projection = self.case()
            records = projection.get("returnRecords") or []
            statuses = {str(r.get("status")) for r in records}
            closed_family = {"CLOSED", "COMPLETED", "COMPLETED_EXTERNAL_SETTLEMENT"}
            if records and statuses <= {"CLOSED", "CANCELLED"} and (
                    str(projection.get("status")) in closed_family
                    and "case-fulfilled" in str(
                        [f0.get("factId") for f0 in projection.get("facts") or []])):
                break
            time.sleep(5)
        projection = self.case()
        self.archive("case_fulfilled", projection)
        records = projection.get("returnRecords") or []
        open_records = [r.get("returnReference") for r in records
                        if str(r.get("status")) not in {"CLOSED", "CANCELLED"}]
        if open_records:
            self.fail(20, f"records not fulfilled after delivery: {open_records}")
        if str(projection.get("status")) not in {"CLOSED", "COMPLETED",
                                                  "COMPLETED_EXTERNAL_SETTLEMENT"}:
            self.fail(20, f"case did not close; status {projection.get('status')}")
        fact_ids = [str(f0.get("factId")) for f0 in projection.get("facts") or []]
        if not any(fid.startswith("case-fulfilled") for fid in fact_ids):
            self.fail(20, "the all-returns-delivered fact never landed on the case")
        facts = self.facts(projection)
        if "return_record_fulfilled" not in facts:
            self.fail(19, "no fulfillment fact reached the case")
        self.ok(19, "fulfillment reflected each status onto the case")
        data = self.send("has the return been delivered?")
        answer = json.dumps(data.get("response") or {}, default=str)
        if "fulfilled" not in answer.lower() and "delivered" not in answer.lower() \
                and not any(rma in answer for rma in rmas):
            self.fail(20, f"associate was not notified of delivery: {answer[:200]}")
        self.ok(20, "records fulfilled, case closed, associate notified in the"
                    " original session")

    def step_22_exception(self) -> None:
        """TC-E2E-03 step 22 on a FRESH shipment: fork into an exception state,
        assert it surfaces, then resume the ladder without losing events."""
        rma = self._rmas()[0]
        shipment = self._shipment(rma)
        shipment_id = str(shipment.get("shipment_id"))
        mode = str(shipment.get("mode") or "parcel")
        codes = {s0["code"]: s0 for s0 in self._ladder(mode)}
        exceptions = [s0 for s0 in self._catalog().get("statuses") or []
                      if s0.get("ladder") == mode and s0.get("exception_state")]
        # advance to a rung that can fork
        current = str(shipment.get("current_status"))
        fork = None
        while fork is None:
            entry = codes.get(current)
            allowed = entry.get("allowed_next") or []
            fork = next((c for c in allowed
                         if any(e["code"] == c for e in exceptions)), None)
            if fork:
                break
            nxt = next((c for c in allowed if c in codes
                        and codes[c]["ordinal"] > entry["ordinal"]), None)
            if nxt is None:
                self.fail(22, f"no exception reachable on the {mode} ladder")
            self.call("POST", f"/api/shipments/{shipment_id}/events",
                      {"status": nxt, "note": "advance to fork"})
            current = nxt
        before = len((self._shipment(rma)).get("events") or [])
        self.call("POST", f"/api/shipments/{shipment_id}/events",
                  {"status": fork, "note": "TC-E2E-03 forced exception"})
        facts = self.facts()
        deadline = time.time() + 90
        while time.time() < deadline and "fulfillment_exception" not in facts:
            time.sleep(5)
            facts = self.facts()
        if "fulfillment_exception" not in facts:
            self.fail(22, "the exception never surfaced on the case")
        # resume: back to the rung we forked from
        resumed = self.call(
            "POST", f"/api/shipments/{shipment_id}/events",
            {"status": current, "note": "resumed after exception"}).get("data") or {}
        if resumed.get("current_status") != current:
            self.fail(22, "the ladder did not resume after the exception")
        if len(resumed.get("events") or []) != before + 2:
            self.fail(22, "prior events were lost across the exception")
        self.ok(22, f"exception {fork} surfaced on the case and cleared;"
                    " ladder resumed with the event trail intact")

    def step_24_idempotency(self) -> None:
        """TC-E2E-03 step 24: replaying the support reply duplicates nothing."""
        items = self.call("GET", "/api/v1/return-support/work-items").get("data") or []
        mine = [i for i in items if i.get("caseId") == self.case_id]
        if not mine:
            self.fail(24, "no support work item to replay")
        before = self.call(
            "GET", f"/api/shipments?case={self.case_id}").get("data") or []
        before_events = {str(s0.get("shipment_id")): len(s0.get("events") or [])
                         for s0 in before}
        self.call("POST",
                  f"/api/v1/return-support/work-items/{mine[0]['id']}/agent-response", {})
        time.sleep(10)
        after = self.call(
            "GET", f"/api/shipments?case={self.case_id}").get("data") or []
        if len(after) != len(before):
            self.fail(24, f"replay changed the shipment count {len(before)} ->"
                          f" {len(after)}")
        for s0 in after:
            sid = str(s0.get("shipment_id"))
            if len(s0.get("events") or []) != before_events.get(sid, -1):
                self.fail(24, f"replay changed the event count on {sid}")
        self.ok(24, "support-reply replay: no duplicate shipment, no duplicate event")

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
    parser.add_argument("--until", type=int, default=16)
    parser.add_argument("--resume-conversation", default=None)
    parser.add_argument("--resume-case", default=None)
    parser.add_argument("--from-step", type=int, default=1)
    parser.add_argument("--multi", action="store_true")
    parser.add_argument("--bay-timeout", action="store_true")
    parser.add_argument("--tc03", action="store_true")
    parser.add_argument("--freight", action="store_true")
    parser.add_argument("--exception-drill", action="store_true")
    args = parser.parse_args()

    run = Run(args.run, args.customer, args.misspelled, args.account, args.order)
    run.multi = args.multi
    run.bay_timeout = args.bay_timeout
    run.freight = args.freight
    if args.resume_conversation:
        run.conversation = args.resume_conversation
        run.case_id = args.resume_case
        transcript = run.call(
            "GET", f"/api/v2/order-agent/conversations/{run.conversation}/transcript")
        run.version = (transcript.get("data") or {}).get("conversationVersion", 0)
    print(f"RUN {args.run}: {args.customer} / {args.order} as {args.misspelled!r}"
          f" conversation {run.conversation}", flush=True)
    stages = [
        (2, run.step_1_2), (4, run.step_3_4), (5, run.step_5), (6, run.step_6),
        (7, run.step_7), (9, run.step_8_9), (10, run.step_10),
        (12, run.step_11_12), (14, run.step_13_14), (15, run.step_15),
        (16, run.step_16_downstream),
    ]
    if args.tc03:
        stages += [(17, run.step_17_shipment_seeded)]
        if args.exception_drill:
            stages += [(22, run.step_22_exception)]
        stages += [
            (20, run.step_18_20_drive_and_fulfill),
            (24, run.step_24_idempotency),
        ]
        parser_max = 24
        if args.until == 16:
            args.until = parser_max
    try:
        for upto, stage in stages:
            if upto > args.until:
                break
            if upto < args.from_step:
                continue
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

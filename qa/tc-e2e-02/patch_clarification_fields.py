"""Add the TC-E2E-02 elicitation facts to clarification_policy via a release.

`ordered_quantity`, `branch_location` and `proof_reference` are return details
the flow elicits in chat; a fact name the policy does not define is reported
and discarded, so they must be operator-declared. Merge-patch replaces arrays
wholesale, so the full current list is read from the running configuration and
re-sent with the additions.
"""

from __future__ import annotations

import json
import time
import urllib.request

BASE = "http://localhost:8000"


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read() or b"{}")


runtime = call("GET", "/api/config/runtime")["data"]
head = runtime["head_revision"]
fields = list(runtime["configuration"]["clarification_policy"]["fields"])
present = {f["field"] for f in fields}
additions = [
    {"field": "ordered_quantity", "priority": 44, "label": "quantity being returned"},
    {"field": "branch_location", "priority": 43, "label": "branch handling the return"},
    {"field": "proof_reference", "priority": 42, "label": "proof of condition reference"},
]
new = [a for a in additions if a["field"] not in present]
if not new:
    print("already present; nothing to publish")
    raise SystemExit(0)

template = {k: v for k, v in fields[-1].items()}
merged = fields + [{**template, **a} for a in new]
release_id = f"tce2e02-elicitation-facts-{time.strftime('%Y%m%d-%H%M%S')}"
print("publishing", release_id, "with", [a["field"] for a in new])
call("POST", "/api/config/releases", {"release_id": release_id, "from_active": True})
call("PATCH", f"/api/config/releases/{release_id}/domains/RETURN_PLATFORM",
     {"patch": {"clarification_policy": {"fields": merged}}})
call("POST", f"/api/config/releases/{release_id}/promote",
     {"status": "VALIDATED", "expected_head_revision": None})
call("POST", f"/api/config/releases/{release_id}/promote",
     {"status": "RELEASED", "expected_head_revision": head})
after = call("GET", "/api/config/runtime")["data"]
print("active:", after["release_id"])

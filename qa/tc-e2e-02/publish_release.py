"""Publish one merge-patch as a release over HTTP — the same pipeline the console runs.

Usage: publish_release.py <release-prefix> <domain-key> <patch-json-or-@file>
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://localhost:8000"


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read() or b"{}")


def main() -> None:
    prefix, domain, patch_arg = sys.argv[1], sys.argv[2], sys.argv[3]
    patch = (
        json.loads(open(patch_arg[1:], encoding="utf-8").read())
        if patch_arg.startswith("@")
        else json.loads(patch_arg)
    )
    runtime = call("GET", "/api/config/runtime")["data"]
    head = runtime["head_revision"]
    release_id = f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}"
    print("active:", runtime["release_id"], "head:", head, "-> new:", release_id)
    call("POST", "/api/config/releases", {"release_id": release_id, "from_active": True})
    call("PATCH", f"/api/config/releases/{release_id}/domains/{domain}", {"patch": patch})
    call("POST", f"/api/config/releases/{release_id}/promote",
         {"status": "VALIDATED", "expected_head_revision": None})
    call("POST", f"/api/config/releases/{release_id}/promote",
         {"status": "RELEASED", "expected_head_revision": head})
    after = call("GET", "/api/config/runtime")["data"]
    print("now active:", after["release_id"], "head:", after["head_revision"])


if __name__ == "__main__":
    main()

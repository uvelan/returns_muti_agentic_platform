#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

attestation="$EVIDENCE_DIR/manual-screen-validation.json"
catalog="$LINUX_SCRIPT_DIR/mandatory_routes.json"
commit="$(git -C "$REPO_ROOT" rev-parse HEAD)"
fingerprint="$(repo_fingerprint)"

if [[ ! -f "$attestation" ]]; then
  python3 - "$attestation" "$catalog" "$commit" "$fingerprint" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
catalog = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
routes = catalog.get("routes")
if not isinstance(routes, list) or len(routes) != 23:
    raise SystemExit("Mandatory route catalog must contain exactly 23 routes.")

route_entries = []
for item in routes:
    if not isinstance(item, dict):
        raise SystemExit("Mandatory route catalog entries must be objects.")
    route = item.get("route")
    dynamic = item.get("dynamic")
    if not isinstance(route, str) or not route.startswith("/"):
        raise SystemExit("Mandatory route catalog contains an invalid route.")
    if not isinstance(dynamic, bool):
        raise SystemExit(f"Mandatory route {route} has an invalid dynamic flag.")
    route_entries.append(
        {
            "route": route,
            "dynamic": dynamic,
            "resolvedUrl": "" if dynamic else route,
            "status": "PENDING",
            "notes": "",
        }
    )

payload = {
    "schemaVersion": 2,
    "commit": sys.argv[3],
    "treeFingerprint": sys.argv[4],
    "operator": "",
    "checkedAt": "",
    "status": "PENDING",
    "routes": route_entries,
}
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
  cat >&2 <<EOF
Manual screen attestation created at:
  $attestation

Inspect all 23 routes using real data. For dynamic routes, record the concrete
resolvedUrl used during inspection. Set every route status to PASS, add operator
and a timezone-aware checkedAt value, set top-level status to PASS, then rerun
with --resume. Automated checks do not replace this gate.
EOF
  exit 1
fi

python3 - "$attestation" "$catalog" "$commit" "$fingerprint" <<'PY'
import datetime as dt
import json
import pathlib
import re
import sys
from urllib.parse import urlparse

path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text(encoding="utf-8"))
catalog = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
catalog_routes = catalog.get("routes")
if not isinstance(catalog_routes, list) or len(catalog_routes) != 23:
    raise SystemExit("Mandatory route catalog must contain exactly 23 routes.")
expected_routes = {
    item["route"]: item["dynamic"]
    for item in catalog_routes
    if isinstance(item, dict)
    and isinstance(item.get("route"), str)
    and isinstance(item.get("dynamic"), bool)
}
if len(expected_routes) != 23:
    raise SystemExit("Mandatory route catalog contains invalid or duplicate entries.")

routes = value.get("routes")
if not isinstance(routes, list):
    raise SystemExit("Manual screen attestation routes must be a list.")
observed = [item.get("route") for item in routes if isinstance(item, dict)]
if len(observed) != len(set(observed)) or set(observed) != set(expected_routes):
    raise SystemExit("Manual screen attestation must contain each mandatory route exactly once.")
if value.get("schemaVersion") != 2:
    raise SystemExit("Manual screen attestation schemaVersion must be 2.")
if value.get("commit") != sys.argv[3] or value.get("treeFingerprint") != sys.argv[4]:
    raise SystemExit("Manual screen attestation does not match the current commit/tree.")
if value.get("status") != "PASS":
    raise SystemExit("Manual screen attestation top-level status is not PASS.")
if not str(value.get("operator", "")).strip():
    raise SystemExit("Manual screen attestation requires operator.")

checked_at = str(value.get("checkedAt", "")).strip()
if not checked_at:
    raise SystemExit("Manual screen attestation requires operator and checkedAt.")
try:
    parsed_checked_at = dt.datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
except ValueError as exc:
    raise SystemExit("Manual screen attestation checkedAt must be ISO 8601.") from exc
if parsed_checked_at.tzinfo is None or parsed_checked_at.utcoffset() is None:
    raise SystemExit("Manual screen attestation checkedAt must include a timezone.")

failed = [item["route"] for item in routes if item.get("status") != "PASS"]
if failed:
    raise SystemExit("Routes without PASS: " + ", ".join(failed))

for item in routes:
    route = item["route"]
    dynamic = item.get("dynamic")
    if dynamic is not expected_routes[route]:
        raise SystemExit(f"Route {route} has an incorrect dynamic flag.")
    resolved_url = str(item.get("resolvedUrl", "")).strip()
    if not resolved_url:
        raise SystemExit(f"Route {route} requires resolvedUrl evidence.")
    resolved_path = urlparse(resolved_url).path
    if expected_routes[route]:
        pattern = re.sub(r":[^/]+", r"[^/]+", re.escape(route).replace(r"\:", ":"))
        if re.fullmatch(pattern, resolved_path) is None or ":" in resolved_path:
            raise SystemExit(f"Dynamic route {route} has invalid resolvedUrl evidence.")
    elif resolved_path != route:
        raise SystemExit(f"Static route {route} resolvedUrl must match the route exactly.")

print("Validated manual PASS attestation for all 23 mandatory routes.")
PY

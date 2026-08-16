#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

# The route catalog is DERIVED from `frontend/src/domains/registry.ts`, not read
# from a committed list.
#
# It used to read `scripts/linux/mandatory_routes.json` and require exactly 23
# routes. Wave G3 deleted that file -- `05_run_contract_and_config_checks.sh`
# says so in its own comment -- so this phase could not run at all: it failed on
# a missing catalog before it reached the attestation. And 23 was wrong twice
# over by then, because Wave F4 replaced the 76-route legacy shell with the nine
# canonical domains.
#
# Deriving it means the count cannot go stale again. A domain or section added
# to the registry is a route an operator must inspect, with no second list to
# remember to update.
attestation="$EVIDENCE_DIR/manual-screen-validation.json"
registry="$REPO_ROOT/frontend/src/domains/registry.ts"
catalog="$EVIDENCE_DIR/mandatory-routes.generated.json"
commit="$(git -C "$REPO_ROOT" rev-parse HEAD)"
fingerprint="$(repo_fingerprint)"

[[ -f "$registry" ]] || {
  echo "Domain registry is missing: $registry" >&2
  exit 1
}

python3 - "$registry" "$catalog" <<'PY'
"""Derive the addressable route catalog from the canonical domain registry.

The shell renders the landing page, one route per domain, and one route per
declared section as `/{domain}/{slug}`. `toSlug` in the registry lowercases,
collapses every non-alphanumeric run to `-`, and trims leading/trailing `-`;
this reproduces exactly that, because a slug computed differently here would
send an operator to a URL the shell does not serve.
"""

import json
import pathlib
import re
import sys

source = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
target = pathlib.Path(sys.argv[2])


def to_slug(label: str) -> str:
    return re.sub(r"(^-|-$)", "", re.sub(r"[^a-z0-9]+", "-", label.lower()))


# `export const NAME = [ "A", "B" ] as const;`
section_arrays: dict[str, list[str]] = {}
for match in re.finditer(
    r"export const (\w+_SECTIONS)\s*=\s*\[(.*?)\]\s*as const;", source, re.DOTALL
):
    name, body = match.group(1), match.group(2)
    section_arrays[name] = re.findall(r'"([^"]+)"', body)

# Each domain object contributes its `path` and whichever `sections:` form
# follows it. `sections(NAME)` expands the named array; `sections: []` is a
# domain that is one workspace.
domains: list[tuple[str, list[str]]] = []
for match in re.finditer(
    r'path:\s*"(/[^"]*)".*?sections:\s*(?:sections\((\w+)\)|(\[\s*\]))',
    source,
    re.DOTALL,
):
    path, named, empty = match.group(1), match.group(2), match.group(3)
    if named is not None:
        if named not in section_arrays:
            raise SystemExit(f"Domain {path} names an unknown section array: {named}")
        domains.append((path, list(section_arrays[named])))
    elif empty is not None:
        domains.append((path, []))

if not domains:
    raise SystemExit(
        "No domains were parsed from the registry. The registry's shape changed; "
        "fix this parser rather than lowering the gate."
    )

landing = re.search(r'export const LANDING_PATH\s*=\s*"([^"]+)"', source)
if landing is None:
    raise SystemExit("LANDING_PATH is missing from the registry.")

routes: list[dict[str, object]] = [{"route": landing.group(1), "dynamic": False}]
for path, labels in domains:
    routes.append({"route": path, "dynamic": False})
    for label in labels:
        routes.append({"route": f"{path}/{to_slug(label)}", "dynamic": False})

seen = [str(item["route"]) for item in routes]
if len(seen) != len(set(seen)):
    raise SystemExit("The derived route catalog contains duplicates.")

target.write_text(
    json.dumps({"routes": routes}, indent=2) + "\n",
    encoding="utf-8",
)
print(f"derived_mandatory_routes={len(routes)}")
PY

if [[ ! -f "$attestation" ]]; then
  python3 - "$attestation" "$catalog" "$commit" "$fingerprint" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
catalog = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
routes = catalog["routes"]

payload = {
    "schemaVersion": 2,
    "commit": sys.argv[3],
    "treeFingerprint": sys.argv[4],
    "operator": "",
    "checkedAt": "",
    "status": "PENDING",
    "routes": [
        {
            "route": item["route"],
            "dynamic": item["dynamic"],
            "resolvedUrl": "" if item["dynamic"] else item["route"],
            "status": "PENDING",
            "notes": "",
        }
        for item in routes
    ],
}
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(f"manual_attestation_routes={len(routes)}")
PY
  cat >&2 <<EOF
Manual screen attestation created at:
  $attestation

Inspect every route it lists using real data. For dynamic routes, record the
concrete resolvedUrl used during inspection. Set every route status to PASS, add
operator and a timezone-aware checkedAt value, set top-level status to PASS, then
rerun with --resume. Automated checks do not replace this gate.
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
expected_routes = {item["route"]: item["dynamic"] for item in catalog["routes"]}
if len(expected_routes) != len(catalog["routes"]):
    raise SystemExit("The derived route catalog contains duplicate entries.")

routes = value.get("routes")
if not isinstance(routes, list):
    raise SystemExit("Manual screen attestation routes must be a list.")
observed = [item.get("route") for item in routes if isinstance(item, dict)]
if len(observed) != len(set(observed)) or set(observed) != set(expected_routes):
    missing = sorted(set(expected_routes) - set(observed))
    extra = sorted(set(observed) - set(expected_routes))
    raise SystemExit(
        "Manual screen attestation must contain each route exactly once. "
        f"Missing: {missing or 'none'}. Unexpected: {extra or 'none'}. "
        "Delete the attestation file to regenerate it against the current registry."
    )
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

print(f"Validated manual PASS attestation for all {len(routes)} mandatory routes.")
PY

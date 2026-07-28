#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
API="${RETURN_PLATFORM_API:-http://127.0.0.1:8000}"
declare -a paths=(
  "/health/live"
  "/health/ready"
  "/api/v1/system/dependencies"
  "/api/v1/seed-data"
  "/api/v1/ai-gateway/routes"
  "/api/v1/dependency-simulator/summary"
)
: >"$EVIDENCE_DIR/api-probes.tsv"
for path in "${paths[@]}"; do
  code="$(curl --silent --show-error --output "$EVIDENCE_DIR/probe-$(echo "$path" | tr '/?' '__').json" \
    --write-out '%{http_code}' "$API$path")"
  printf '%s\t%s\n' "$code" "$path" >>"$EVIDENCE_DIR/api-probes.tsv"
  [[ "$code" =~ ^2[0-9][0-9]$ ]] || {
    printf 'API probe failed: %s returned HTTP %s\n' "$path" "$code" >&2
    exit 1
  }
done

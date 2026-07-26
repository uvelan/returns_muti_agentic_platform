#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
cd "$REPO_ROOT"

readonly max_attempts=3
readonly wait_timeout_seconds=300

for attempt in $(seq 1 "$max_attempts"); do
  printf '[infra] compose convergence attempt %s/%s\n' "$attempt" "$max_attempts"

  if docker compose up -d --wait --wait-timeout "$wait_timeout_seconds"; then
    tmp_evidence="$EVIDENCE_DIR/infrastructure-services.json.tmp"
    docker compose ps --format json >"$tmp_evidence"
    mv "$tmp_evidence" "$EVIDENCE_DIR/infrastructure-services.json"
    exit 0
  fi

  printf '[infra] attempt %s did not converge; current state follows\n' "$attempt" >&2
  docker compose ps -a >&2 || true

  if [[ "$attempt" -lt "$max_attempts" ]]; then
    # Keep the same containers and volumes. Temporal and the MongoDB replica set
    # can be transiently unhealthy while their internal initialization converges.
    sleep 30
  fi
done

printf '[infra] services failed to converge after %s attempts\n' "$max_attempts" >&2
docker compose logs --no-color --tail=200 \
  sqlserver sqlserver-init \
  mongodb mongodb-rs-init \
  neo4j valkey \
  temporal-postgresql temporal temporal-ui >&2 || true
exit 1

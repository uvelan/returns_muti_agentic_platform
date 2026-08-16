#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
cd "$REPO_ROOT"

readonly infrastructure_timeout_seconds=420
readonly init_timeout_seconds=300
readonly poll_interval_seconds=5

# `vault` belongs here and was missing. Every phase after this one -- 07's seed,
# 08's backend, 09's workers -- resolves its datastore credentials through
# Vault, and with no Vault running they all fail on the `.env` sentinel
# `mongodb://vault-resolved.invalid/...`. The pipeline could not bring up a
# clean machine at all; it only ever appeared to work where `infra.sh start`
# (which does list `vault`) had been run by hand first.
readonly -a base_services=(
  vault
  sqlserver
  mongodb
  neo4j
  valkey
  temporal-postgresql
)
readonly -a init_services=(
  sqlserver-init
  mongodb-rs-init
)
readonly -a steady_state_services=(
  vault
  sqlserver
  mongodb
  neo4j
  valkey
  temporal-postgresql
  temporal
  temporal-ui
)

container_id_for() {
  # `--profile dev-tools` so `temporal-ui` is visible here. Without it the
  # lookup returns nothing, the readiness loop reads that as "not created yet",
  # and the phase burns its full seven-minute timeout on a container that is
  # running perfectly well.
  docker compose --profile dev-tools ps --all --quiet "$1" | head -n 1
}

dump_infrastructure_diagnostics() {
  printf '\n[infra] compose state\n' >&2
  docker compose ps --all >&2 || true
  printf '\n[infra] recent logs\n' >&2
  docker compose --profile dev-tools logs --no-color --tail=200 \
    vault \
    sqlserver sqlserver-init \
    mongodb mongodb-rs-init \
    neo4j valkey \
    temporal-postgresql temporal temporal-ui >&2 || true
}

wait_for_services_ready() {
  local timeout_seconds="$1"
  shift
  local -a services=("$@")
  local deadline=$((SECONDS + timeout_seconds))
  local service container_id status health all_ready

  while ((SECONDS < deadline)); do
    all_ready=true

    for service in "${services[@]}"; do
      container_id="$(container_id_for "$service")"
      if [[ -z "$container_id" ]]; then
        all_ready=false
        continue
      fi

      status="$(docker inspect --format '{{.State.Status}}' "$container_id")"
      health="$(
        docker inspect \
          --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
          "$container_id"
      )"

      case "$status" in
        running)
          if [[ "$health" != "healthy" && "$health" != "none" ]]; then
            all_ready=false
          fi
          ;;
        created | restarting)
          all_ready=false
          ;;
        exited | dead)
          printf '[infra] service %s entered terminal state %s\n' \
            "$service" "$status" >&2
          return 1
          ;;
        *)
          printf '[infra] service %s has unexpected state %s\n' \
            "$service" "$status" >&2
          return 1
          ;;
      esac
    done

    if [[ "$all_ready" == "true" ]]; then
      return 0
    fi

    sleep "$poll_interval_seconds"
  done

  printf '[infra] timed out waiting for services: %s\n' "${services[*]}" >&2
  return 1
}

wait_for_init_success() {
  local timeout_seconds="$1"
  shift
  local -a services=("$@")
  local deadline=$((SECONDS + timeout_seconds))
  local service container_id status exit_code all_complete

  while ((SECONDS < deadline)); do
    all_complete=true

    for service in "${services[@]}"; do
      container_id="$(container_id_for "$service")"
      if [[ -z "$container_id" ]]; then
        all_complete=false
        continue
      fi

      status="$(docker inspect --format '{{.State.Status}}' "$container_id")"
      case "$status" in
        exited)
          exit_code="$(docker inspect --format '{{.State.ExitCode}}' "$container_id")"
          if [[ "$exit_code" != "0" ]]; then
            printf '[infra] init service %s exited with code %s\n' \
              "$service" "$exit_code" >&2
            return 1
          fi
          ;;
        created | running | restarting)
          all_complete=false
          ;;
        dead)
          printf '[infra] init service %s entered terminal state dead\n' \
            "$service" >&2
          return 1
          ;;
        *)
          printf '[infra] init service %s has unexpected state %s\n' \
            "$service" "$status" >&2
          return 1
          ;;
      esac
    done

    if [[ "$all_complete" == "true" ]]; then
      return 0
    fi

    sleep "$poll_interval_seconds"
  done

  printf '[infra] timed out waiting for init services: %s\n' \
    "${services[*]}" >&2
  return 1
}

printf '[infra] starting base services\n'
docker compose up -d "${base_services[@]}"
if ! wait_for_services_ready \
  "$infrastructure_timeout_seconds" "${base_services[@]}"; then
  dump_infrastructure_diagnostics
  exit 1
fi

# Vault's healthcheck passes while SEALED -- deliberately, because a sealed
# Vault is a live server. So "vault is healthy" is not "vault is usable", and
# every later phase needs the second. Initialize, unseal and seed here, then
# state the result in one line, because a sealed Vault otherwise presents as six
# unrelated worker crash-loops three phases later.
printf '[infra] initializing, unsealing and seeding Vault\n'
if command -v python3.13 >/dev/null 2>&1; then
  vault_python=python3.13
else
  vault_python=python3
fi
if ! "$vault_python" "$REPO_ROOT/scripts/vault/bootstrap_local_vault.py"; then
  dump_infrastructure_diagnostics
  exit 1
fi
assert_vault_unsealed

printf '[infra] running one-shot initialization services\n'
docker compose up -d "${init_services[@]}"
if ! wait_for_init_success "$init_timeout_seconds" "${init_services[@]}"; then
  dump_infrastructure_diagnostics
  exit 1
fi

printf '[infra] starting Temporal\n'
docker compose up -d temporal
if ! wait_for_services_ready "$infrastructure_timeout_seconds" temporal; then
  dump_infrastructure_diagnostics
  exit 1
fi

# `--profile dev-tools` is required now that `temporal-ui` carries that profile.
# Whether naming a profiled service on the command line implicitly enables its
# profile depends on the Compose version; stating the profile does not.
printf '[infra] starting Temporal UI\n'
docker compose --profile dev-tools up -d temporal-ui
if ! wait_for_services_ready "$infrastructure_timeout_seconds" temporal-ui; then
  dump_infrastructure_diagnostics
  exit 1
fi

if ! wait_for_services_ready \
  "$infrastructure_timeout_seconds" "${steady_state_services[@]}"; then
  dump_infrastructure_diagnostics
  exit 1
fi

readonly tmp_evidence="$EVIDENCE_DIR/infrastructure-services.json.tmp"
docker compose --profile dev-tools ps --all --format json >"$tmp_evidence"
mv "$tmp_evidence" "$EVIDENCE_DIR/infrastructure-services.json"
printf '[infra] all infrastructure services reached steady state\n'

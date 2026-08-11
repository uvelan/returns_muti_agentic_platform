#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-status}"
command -v docker >/dev/null || { echo "Docker is required only for infrastructure commands." >&2; exit 1; }
docker compose version >/dev/null
cd "$ROOT"

# The datastores, and nothing else. A bare `docker compose up -d` also starts
# `runtime-configuration-init`, which is correct for the containerized stack but
# is built from `return-platform-backend:local` -- so asking for infrastructure
# built the entire backend image first, on a machine whose backend was going to
# run on the host anyway. Naming the services is what keeps `start` to its name.
#
# Nothing is skipped by leaving it out: `prepare_runtime_configuration.sh` runs
# the same SQL migrations, Neo4j migrations and graph-configuration bootstrap on
# the host before the backend starts. `full-containerized` below still brings the
# init container up, because there the host runs none of it.
readonly -a infrastructure_services=(
  vault
  mongodb
  mongodb-rs-init
  neo4j
  valkey
  sqlserver
  sqlserver-init
  temporal-postgresql
  temporal
)

python3 "$ROOT/scripts/linux/ensure_runtime_env_keys.py" --env-file "$ROOT/.env"
python3 "$ROOT/scripts/linux/ensure_local_infrastructure_secrets.py"
python3 "$ROOT/scripts/linux/ensure_local_replica_key.py"
python3 "$ROOT/scripts/linux/validate_env.py" "$ROOT/.env"
case "$ACTION" in
  start)
    docker compose up -d --wait "${infrastructure_services[@]}"
    PYTHON_BIN="$(command -v python3.13 || command -v python3)"
    "$PYTHON_BIN" "$ROOT/scripts/vault/bootstrap_local_vault.py"
    ;;
  full-containerized)
    docker compose up -d --wait
    PYTHON_BIN="$(command -v python3.13 || command -v python3)"
    "$PYTHON_BIN" "$ROOT/scripts/vault/bootstrap_local_vault.py"
    docker compose --profile containerized-app up -d --build --wait
    ;;
  stop)
    docker compose down --remove-orphans
    ;;
  status)
    docker compose ps
    ;;
  logs)
    if [[ -n "${2:-}" ]]; then
      docker compose logs -f --tail=200 "$2"
    else
      docker compose logs -f --tail=200
    fi
    ;;
  reset)
    [[ "${CONFIRM_RESET:-}" == "YES" ]] || { echo "Set CONFIRM_RESET=YES to delete infrastructure volumes." >&2; exit 2; }
    docker compose --profile containerized-app down --volumes --remove-orphans
    rm -rf "$ROOT/.vault-local"
    ;;
  config)
    docker compose --profile containerized-app config --quiet
    ;;
  *)
    echo "Usage: $0 {start|full-containerized|stop|status|logs [service]|reset|config}" >&2
    exit 2
    ;;
esac

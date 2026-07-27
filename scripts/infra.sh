#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-status}"
command -v docker >/dev/null || { echo "Docker is required only for infrastructure commands." >&2; exit 1; }
docker compose version >/dev/null
cd "$ROOT"
python3 "$ROOT/scripts/linux/ensure_runtime_env_keys.py" --env-file "$ROOT/.env"
python3 "$ROOT/scripts/linux/ensure_local_infrastructure_secrets.py"
python3 "$ROOT/scripts/linux/ensure_local_replica_key.py"
python3 "$ROOT/scripts/linux/validate_env.py" "$ROOT/.env"
case "$ACTION" in
  start)
    docker compose up -d --wait
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

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
# Split by how you tell that each is finished, which is not a detail:
# `--wait` waits for a service to be running or healthy, and reports a container
# that EXITS as a failure -- even on exit code 0. Passing the one-shot
# initializers to it therefore failed the moment they succeeded:
#
#     container return-multi-agent-platform-mongodb-rs-init-1 exited (0)
#
# and, under `set -e`, took the whole reset down with a line that reads like
# progress. They are waited for by completion instead, below.
readonly -a datastore_services=(
  mongodb
  neo4j
  valkey
  sqlserver
  temporal-postgresql
  temporal
)
readonly -a init_services=(
  mongodb-rs-init
  sqlserver-init
)
# Run the one-shot initializers and require each to succeed. `docker wait`
# blocks until the container stops and prints its exit code, and returns
# immediately for one that has already finished.
run_init_services() {
  docker compose up -d "$@"
  local service container code
  for service in "$@"; do
    container="$(docker compose ps -aq "$service" | head -n 1)"
    if [[ -z "$container" ]]; then
      echo "Initialization service $service did not start." >&2
      return 1
    fi
    code="$(docker wait "$container")"
    if [[ "$code" != "0" ]]; then
      echo "Initialization service $service failed with exit code $code:" >&2
      docker compose logs --no-color --tail 40 "$service" >&2 || true
      return 1
    fi
  done
}

source "$ROOT/scripts/linux/lib/common.sh"

# `status` is read-only. Rewriting `.env` and
# regenerating credentials on the way to *asking a question* is how a diagnostic
# command becomes a mutation, so only the actions that actually bring services
# up prepare the environment first.
case "$ACTION" in
  start | full-containerized | reset | config)
    python3 "$ROOT/scripts/linux/ensure_runtime_env_keys.py" --env-file "$ROOT/.env"
    python3 "$ROOT/scripts/linux/ensure_local_infrastructure_secrets.py"
    python3 "$ROOT/scripts/linux/ensure_local_replica_key.py"
    python3 "$ROOT/scripts/linux/validate_env.py" "$ROOT/.env"
    ;;
esac

case "$ACTION" in
  start)
    docker compose up -d --wait "${datastore_services[@]}"
    run_init_services "${init_services[@]}"
    ;;
  full-containerized)
    docker compose up -d --wait "${datastore_services[@]}"
    # `runtime-configuration-init` joins them here: migrations and the graph
    # configuration release run in the container when the host runs none of it.
    run_init_services "${init_services[@]}" runtime-configuration-init
    # `--force-recreate`, and it is not belt-and-braces. `docker compose up -d`
    # compares the *service definition*, not the image id, so when `--build`
    # produces a new image under the same `:local` tag the running container is
    # considered up to date and is left alone. The rebuild succeeds, the old
    # code keeps serving, and the next hour goes on "why isn't the fix live".
    docker compose --profile containerized-app up -d --build --force-recreate --wait
    # The frontend's nginx resolves `backend` once, at startup, and caches the
    # address for the life of the process. Recreating the backend gives it a new
    # container IP, after which every `/api/*` call 502s until nginx is restarted
    # too. Frontend last, always -- the line above may have recreated `backend`.
    docker compose --profile containerized-app up -d --force-recreate --wait frontend
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
    ;;
  config)
    docker compose --profile containerized-app config --quiet
    ;;
  *)
    echo "Usage: $0 {start|full-containerized|stop|status|logs [service]|reset|config}" >&2
    exit 2
    ;;
esac

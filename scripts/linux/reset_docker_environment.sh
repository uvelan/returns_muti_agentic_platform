#!/usr/bin/env bash
set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

DELETE_ALL_DOCKER=false
CONFIRM_DELETE_ALL=false
PULL_IMAGES=true
RUN_BOOTSTRAP=true
START_HOST=false

log() {
    printf '[docker-reset] %s\n' "$*"
}

fail() {
    printf '[docker-reset] ERROR: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage:
  reset_docker_environment.sh [options]

Options:
  --all-docker
      Delete all Docker containers, images, volumes, unused networks,
      and builder cache on this Linux host.

  --confirm-delete-all
      Required with --all-docker.

  --no-pull
      Skip pulling images.

  --no-bootstrap
      Skip scripts/bootstrap_host.sh.

  --start-host
      Start backend, workers, and frontend after infrastructure recreation.

  -h, --help
      Show this help.
EOF
}

while (($# > 0)); do
    case "$1" in
        --all-docker)
            DELETE_ALL_DOCKER=true
            ;;
        --confirm-delete-all)
            CONFIRM_DELETE_ALL=true
            ;;
        --no-pull)
            PULL_IMAGES=false
            ;;
        --no-bootstrap)
            RUN_BOOTSTRAP=false
            ;;
        --start-host)
            START_HOST=true
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage
            fail "Unknown argument: $1"
            ;;
    esac
    shift
done

command -v docker >/dev/null 2>&1 || fail "Docker CLI is not installed."
docker info >/dev/null 2>&1 || fail "Docker daemon is not running or inaccessible."

cd "${REPOSITORY_ROOT}"

if [[ ! -f ".env" ]]; then
    if [[ -f ".env.example" ]]; then
        cp ".env.example" ".env"
        chmod 600 ".env"
        fail "Created .env from .env.example. Fill required values and rerun."
    fi
    fail "Root .env is missing."
fi

chmod 600 ".env" || true

if [[ "${DELETE_ALL_DOCKER}" == true && "${CONFIRM_DELETE_ALL}" != true ]]; then
    fail "--all-docker requires --confirm-delete-all."
fi

log "Repository: ${REPOSITORY_ROOT}"
log "Root .env will be preserved."

if [[ -x "scripts/linux/17_stop_host_processes.sh" ]]; then
    log "Stopping host processes."
    scripts/linux/17_stop_host_processes.sh || true
elif [[ -x "scripts/stop_host_processes.sh" ]]; then
    log "Stopping host processes."
    scripts/stop_host_processes.sh || true
fi

if docker compose config >/dev/null 2>&1; then
    log "Stopping current Compose project."
    docker compose down \
        --volumes \
        --remove-orphans \
        --rmi local || true
fi

if [[ "${DELETE_ALL_DOCKER}" == true ]]; then
    log "GLOBAL DESTRUCTIVE MODE ENABLED."

    mapfile -t container_ids < <(docker ps -aq)
    if ((${#container_ids[@]} > 0)); then
        docker rm -f "${container_ids[@]}"
    fi

    mapfile -t image_ids < <(docker images -aq)
    if ((${#image_ids[@]} > 0)); then
        docker rmi -f "${image_ids[@]}" || true
    fi

    mapfile -t volume_names < <(docker volume ls -q)
    if ((${#volume_names[@]} > 0)); then
        docker volume rm -f "${volume_names[@]}" || true
    fi

    docker network prune -f
    docker builder prune -af
    docker system prune -af --volumes
else
    log "Project-only cleanup."
    docker container prune -f
    docker image prune -f
    docker network prune -f
    docker builder prune -f
fi

log "Docker state after cleanup:"
docker ps -a
docker images
docker volume ls

if [[ "${PULL_IMAGES}" == true ]]; then
    if docker compose config >/dev/null 2>&1; then
        log "Pulling fresh Compose images."
        docker compose pull
    fi
fi

if [[ "${RUN_BOOTSTRAP}" == true ]]; then
    [[ -x "scripts/bootstrap_host.sh" ]] || fail "scripts/bootstrap_host.sh is missing or not executable."
    log "Running host bootstrap."
    scripts/bootstrap_host.sh
fi

if [[ -x "scripts/infra.sh" ]]; then
    log "Recreating infrastructure."
    scripts/infra.sh up
elif docker compose config >/dev/null 2>&1; then
    docker compose up -d --remove-orphans
else
    fail "No infrastructure start command was found."
fi

log "Current Docker containers:"
docker ps -a

if [[ "${START_HOST}" == true ]]; then
    [[ -x "scripts/run_all_host.sh" ]] || fail "scripts/run_all_host.sh is missing or not executable."
    log "Starting backend, workers, and frontend."
    scripts/run_all_host.sh
fi

log "Docker environment recreation completed."
log "Next command:"
log "  ./scripts/linux/run_full_linux_validation.sh"

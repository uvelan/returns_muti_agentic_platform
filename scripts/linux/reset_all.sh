#!/usr/bin/env bash
# One command to get from any state to a running platform with fresh data.
#
# The pieces existed and the sequence did not. `reset_docker_environment.sh`
# resets infrastructure, `run_all_host.sh` starts the host processes, and
# `07_seed_and_validate_data.sh` seeds the *old synthetic manifest* -- but
# nothing loaded the reference dataset, and nothing at all built the knowledge
# graph. Without that last step Neo4j stays empty and the copilot truthfully
# reports finding no orders, which reads as a broken agent rather than a
# missing build step.
#
# Order matters and is not arbitrary:
#   1. stop the host, so nothing writes while the stores are being dropped
#   2. reset and start infrastructure, so the datastores exist and are healthy
#   3. seed Vault, which step 2 destroyed along with its volume
#   4. load the reference dataset, which drops every database first
#   5. start the host, whose bootstrap recreates the system store it just lost
#   6. build the graph, which needs the source collections from (4)
#
#   Usage: ./scripts/linux/reset_all.sh [options]
#
#     --keep-images       skip the image pull and prune (faster re-runs)
#     --no-host           leave backend, workers and frontend stopped
#     --dataset DIR       load from a different dataset directory
#     -h, --help          show this help
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

KEEP_IMAGES=false
START_HOST=true
DATASET=""

log() { printf '\n[reset-all] %s\n' "$*"; }
fail() { printf '[reset-all] ERROR: %s\n' "$*" >&2; exit 1; }

while (($# > 0)); do
    case "$1" in
        --keep-images) KEEP_IMAGES=true ;;
        --no-host) START_HOST=false ;;
        --dataset)
            shift || fail "--dataset requires a directory"
            DATASET="$1"
            ;;
        -h|--help)
            # Everything from the line after the shebang up to the first line
            # that is not a comment. A fixed line range goes stale the moment
            # the header is edited, and prints `set -Eeuo pipefail` as help.
            sed -n '2,${/^#/!q; s/^# \{0,1\}//p;}' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *) fail "Unknown argument: $1" ;;
    esac
    shift
done

cd "${REPO_ROOT}"
[[ -f .env ]] || fail "Root .env is missing. Run scripts/bootstrap_host.sh first."

# `python` is not a command on a stock Linux host, and the backend venv is what
# carries the platform's own dependencies. Prefer it, and say so plainly rather
# than failing later inside a script with a confusing import error.
if [[ -x "backend/.venv/bin/python" ]]; then
    PYTHON="${REPO_ROOT}/backend/.venv/bin/python"
elif [[ -x "backend/.venv/Scripts/python.exe" ]]; then
    PYTHON="${REPO_ROOT}/backend/.venv/Scripts/python.exe"
else
    fail "backend/.venv is missing. Run scripts/bootstrap_host.sh first."
fi

log "1/6  Stopping host processes"
scripts/linux/17_stop_host_processes.sh || true

log "2/6  Resetting and starting infrastructure"
reset_args=(--no-bootstrap)
[[ "${KEEP_IMAGES}" == true ]] && reset_args+=(--no-pull)
scripts/linux/reset_docker_environment.sh "${reset_args[@]}"

# `docker compose down --volumes` in step 2 takes the Vault volume with it, and
# nothing else in the chain puts it back. Every step after this one resolves its
# datastore credentials through Vault, so without this they all fail on
# "Required Vault secret is unavailable" -- an error that points at secrets
# management rather than at the reset that removed them. Idempotent: a Vault
# that is already initialised and unsealed is left alone.
log "3/6  Initialising and seeding the local Vault"
"${PYTHON}" scripts/vault/bootstrap_local_vault.py

log "4/6  Loading the reference dataset (drops every database first)"
dataset_args=()
[[ -n "${DATASET}" ]] && dataset_args+=("${DATASET}")
"${PYTHON}" backend/scripts/load_reference_dataset.py "${dataset_args[@]}"

if [[ "${START_HOST}" == true ]]; then
    log "5/6  Starting backend, workers and frontend"
    scripts/run_all_host.sh
else
    log "5/6  Skipping host start (--no-host)"
fi

# Last, and only after the load: the graph is built from the source collections
# written in step 3, and a build against an empty source silently produces an
# empty graph.
log "6/6  Building the knowledge graph"
"${PYTHON}" backend/scripts/build_knowledge_graph.py

log "Done. The platform is running against a freshly loaded dataset."
if [[ "${START_HOST}" == true ]]; then
    log "  Console:  http://localhost:5173"
    log "  API:      http://localhost:8000/docs"
fi

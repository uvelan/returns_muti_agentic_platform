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
#     --graph-records N   per-asset cap for the graph build (default 30000)
#     -h, --help          show this help
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

KEEP_IMAGES=false
START_HOST=true
DATASET=""
# 30,000 rather than the script default of 5,000, and it matters.
# `GraphSyncRequest.maxRecordsPerAsset` defaults to 1,000 and the effective
# limit is `min(maxRecordsPerAsset, PLATFORM_GRAPH_SYNC_MAX_RECORDS)`, where
# that setting defaults to 10,000 -- exactly the `customers` count in
# `backend/config/seed/e2e_seed_manifest.json`. No headroom at all: a graph
# built at the defaults silently truncates the corpus, the copilot then finds
# nothing for every customer past the cut, and it reads as a broken agent.
GRAPH_RECORDS=30000

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
        --graph-records)
            shift || fail "--graph-records requires a positive integer"
            [[ "$1" =~ ^[1-9][0-9]*$ ]] || fail "--graph-records must be a positive integer"
            GRAPH_RECORDS="$1"
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
# Not redundant with the line above. The bootstrap unseals only when it observes
# `sealed: true`, and it does not re-check afterwards -- an unseal that was
# accepted but did not open Vault (a wrong or partial key) leaves it sealed with
# no error here. Everything from step 4 on then fails against the `.env`
# sentinel `mongodb://vault-resolved.invalid/...`, which reads as a dozen
# separate connection bugs rather than as one sealed store.
source "${REPO_ROOT}/scripts/linux/lib/common.sh"
# `common.sh` runs `set -euo pipefail`, which drops the `-E` this script opened
# with. Restore it rather than inherit a quietly weaker shell.
set -Eeuo pipefail
assert_vault_unsealed || fail "Vault did not open. Run: ./scripts/infra.sh unseal"

log "4/6  Loading the reference dataset (drops every database first)"
dataset_args=()
[[ -n "${DATASET}" ]] && dataset_args+=("${DATASET}")
"${PYTHON}" backend/scripts/load_reference_dataset.py "${dataset_args[@]}"

if [[ "${START_HOST}" == true ]]; then
    log "5/6  Starting backend, workers and frontend"
    # `--no-supervise`, and without it this whole script was broken. The
    # supervising form never returns, so step 6 -- the graph build, the one step
    # this script exists to add -- was unreachable, and the Ctrl-C that ended
    # the apparent hang ran the supervisor's EXIT trap and stopped everything.
    scripts/run_all_host.sh --no-supervise
else
    log "5/6  Skipping host start (--no-host)"
fi

# Last, and only after the load: the graph is built from the source collections
# written in step 4, and a build against an empty source silently produces an
# empty graph. `build_knowledge_graph.py` refuses to report success on a
# COMPLETED run that wrote no nodes or relationships, which is the guard against
# the worst version of this: the source scan bounds on `{cursor: {"$lte": Date}}`
# and MongoDB compares only within BSON type brackets, so a timestamp stored as
# a STRING matches no date bound at all. Zero records scanned, run status
# COMPLETED, and a graph holding nothing gets activated.
log "6/6  Building the knowledge graph (cap ${GRAPH_RECORDS} records per asset)"
# The env var raises the second ceiling. `maxRecordsPerAsset` alone cannot get
# past `PLATFORM_GRAPH_SYNC_MAX_RECORDS`, so passing 30000 without this would
# still clamp to 10,000.
PLATFORM_GRAPH_SYNC_MAX_RECORDS="${PLATFORM_GRAPH_SYNC_MAX_RECORDS:-${GRAPH_RECORDS}}" \
    "${PYTHON}" backend/scripts/build_knowledge_graph.py "${GRAPH_RECORDS}"

if [[ "${START_HOST}" == true ]]; then
    # Verify rather than assume. Running processes are not a working platform:
    # `/health/ready` answers 200 while the workers are still on the previous
    # release, so both questions get asked. ACTIVATED IS NOT LIVE.
    log "Verifying the running platform"
    curl --fail --silent --show-error http://127.0.0.1:8000/health/ready >/dev/null \
        || fail "Backend is not ready. Check .runtime/linux-validation/logs/backend.log"
    if ! scripts/linux/12_validate_worker_heartbeats.sh; then
        fail "Workers are up but the release is not LIVE across every required process class."
    fi
fi

log "Done. The platform is running against a freshly loaded dataset."
if [[ "${START_HOST}" == true ]]; then
    log "  Console:  http://localhost:5173"
    log "  API:      http://localhost:8000/docs"
    log "  Adoption: http://localhost:8000/api/config/adoption"
fi

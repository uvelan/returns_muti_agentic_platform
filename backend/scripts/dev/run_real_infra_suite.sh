#!/usr/bin/env bash
# The canonical real-infra test invocation.
#
# For eight commits the suite was reported as "99 pre-existing errors, accepted
# baseline". None of them were code defects -- every one was this invocation
# being wrong in two ways:
#
#   95 errors  tests/conftest.py's `test_settings` fixture requires
#              NVIDIA_API_KEY/GOOGLE_API_KEY (rotated out by commit fbfcf05 and
#              never replaced). It only reads them to populate Settings fields;
#              nothing in the affected tests makes a provider call, so any
#              non-empty placeholder satisfies them. Real credentials are NOT
#              needed -- and if you want to exercise a real reasoning turn, use
#              the MANUAL provider (see tests/test_manual_provider_reasoning_e2e.py),
#              which needs no key at all.
#
#    4 errors  .env's PLATFORM_SQLSERVER_PORT is the *host* published port
#              (14330). Inside the compose network SQL Server listens on 1433,
#              so a container-side run must override it.
#
# With both fixed the suite is green. Placeholders are passed as process env,
# never written to .env -- they are fake values and must not become config.
#
# Usage:  bash scripts/dev/run_real_infra_suite.sh [extra pytest args]
set -euo pipefail

CONTAINER="${REAL_INFRA_CONTAINER:-c2-test-runner}"
REPO_BACKEND="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO_ROOT="$(cd "${REPO_BACKEND}/.." && pwd)"

# Neo4j-backed tests authenticate with GRAPH_PASSWORD -- the same variable
# compose.yaml uses for NEO4J_AUTH. It is deliberately NOT defaulted anywhere:
# a wrong guess trips Neo4j's authentication rate limiter, which then fails
# every Neo4j test in the run and needs a container restart to clear. Sourced
# from the repo .env so the value stays in one place and never lands in a
# script or a container image.
if [[ -z "${GRAPH_PASSWORD:-}" && -f "${REPO_ROOT}/.env" ]]; then
  GRAPH_PASSWORD="$(grep -E '^GRAPH_PASSWORD=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"
fi
: "${GRAPH_PASSWORD:?set GRAPH_PASSWORD (or provide it in the repo .env) before running}"

# Wipe before syncing. `docker cp` MERGES into the destination -- it never
# removes files that exist there but not in the source -- and this container is
# shared with any other session working the same repo. A merge-sync once
# produced a "green" run that was executing a mixture of two sessions' code.
echo "==> wiping and re-syncing ${CONTAINER} from ${REPO_BACKEND}"
docker exec "${CONTAINER}" bash -lc \
  'rm -rf /workspace_root/backend/src /workspace_root/backend/tests /workspace_root/backend/config'
docker cp "${REPO_BACKEND}/src" "${CONTAINER}:/workspace_root/backend/"
docker cp "${REPO_BACKEND}/tests" "${CONTAINER}:/workspace_root/backend/"
docker cp "${REPO_BACKEND}/config" "${CONTAINER}:/workspace_root/backend/"
# Host-compiled bytecode carries Windows-baked co_filename metadata that
# produces baffling path errors when reused under Linux.
docker exec "${CONTAINER}" bash -lc \
  'find /workspace_root/backend -name __pycache__ -exec rm -rf {} + 2>/dev/null || true'

# Excluded, and why:
#   test_order_agent_rest.py        40 scenarios that each need a real model
#                                   response; use the MANUAL provider by hand.
#   test_ai_model_probe_evaluator   \
#   gate_tools/                      | need repo_root/scripts/, which this
#   test_runtime_env_key_sync.py    /  backend-only container copy omits.
echo "==> running suite"
docker exec \
  -e PLATFORM_TEST_MONGO_HOST=mongodb \
  -e PLATFORM_TEST_NEO4J_HOST=neo4j \
  -e PLATFORM_TEST_SQLSERVER_HOST=sqlserver \
  -e PLATFORM_SQLSERVER_PORT=1433 \
  -e PLATFORM_TEST_TEMPORAL_TARGET=temporal:7233 \
  -e GRAPH_PASSWORD="${GRAPH_PASSWORD}" \
  -e NVIDIA_API_KEY=placeholder-not-a-real-key \
  -e GOOGLE_API_KEY=placeholder-not-a-real-key \
  "${CONTAINER}" bash -lc "cd /workspace_root/backend && /opt/venv/bin/python -m pytest tests/ \
    --ignore=tests/test_order_agent_rest.py \
    --ignore=tests/test_ai_model_probe_evaluator.py \
    --ignore=tests/gate_tools \
    --ignore=tests/test_runtime_env_key_sync.py \
    -q $*"

#!/usr/bin/env bash
# The canonical real-infra test invocation.
#
# Runs the suite in the `diagnostics` service, which Compose builds from
# `backend/Dockerfile` (target `test`) and which bind-mounts the working tree
# read-only at /workspace_root/backend.
#
# It used to target `c2-test-runner`: a container nothing in this repository
# created, kept in sync by `docker cp`. `docker cp` MERGES into the destination
# and never removes files that are absent from the source, and the container
# was shared with any other session working the same repo -- so a file left
# behind by one session once produced a "green" run for code that was not in
# the tree. There is no sync step here at all now, and nothing to go stale.
#
# For eight commits before that, the suite was reported as "99 pre-existing
# errors, accepted baseline". None were code defects; every one was this
# invocation being wrong in two ways (missing placeholder AI keys, and the host
# SQL Server port used in-network). Both are now fixed in the service
# definition rather than passed here, so a plain `docker compose exec` into the
# container reproduces exactly what this script runs.
#
# Usage:  bash scripts/dev/run_real_infra_suite.sh [extra pytest args]
set -euo pipefail

SERVICE="${REAL_INFRA_SERVICE:-diagnostics}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
COMPOSE=(docker compose --profile dev-tools)

cd "${REPO_ROOT}"

# `--wait` blocks on the datastore health conditions the service declares, so a
# suite run cannot start against a half-up stack and report infrastructure
# timing as test failures. Idempotent: a no-op when everything is already up.
echo "==> ensuring ${SERVICE} and its dependencies are up"
"${COMPOSE[@]}" up -d --wait "${SERVICE}"

# Excluded, and why:
#   test_order_agent_rest.py        40 scenarios that each need a real model
#                                   response; use the MANUAL provider by hand.
#   test_ai_model_probe_evaluator   \
#   gate_tools/                      | need repo_root/scripts/, which the
#   test_runtime_env_key_sync.py    /  backend-only bind mount omits.
#
# `-p no:cacheprovider`: the mount is read-only, and .pytest_cache is the one
# thing pytest writes to rootdir. Nothing else needs to write there, which is
# the point of mounting it read-only.
# `MSYS_NO_PATHCONV=1`: under Git Bash on Windows, MSYS rewrites any argument
# that looks like an absolute POSIX path into a Windows one before the process
# sees it, so `/opt/venv/bin/python` reached Docker as
# `C:/Program Files/Git/opt/venv/bin/python` and the exec failed with a
# "no such file or directory" that looks like a broken image rather than a
# mangled argument. The paths here are inside the container and must be passed
# through untouched. No effect on Linux or macOS, where the variable is unread.
echo "==> running suite"
MSYS_NO_PATHCONV=1 "${COMPOSE[@]}" exec -T "${SERVICE}" /opt/venv/bin/python -m pytest tests/ \
  -p no:cacheprovider \
  --ignore=tests/test_order_agent_rest.py \
  --ignore=tests/test_ai_model_probe_evaluator.py \
  --ignore=tests/gate_tools \
  --ignore=tests/test_runtime_env_key_sync.py \
  -q "$@"

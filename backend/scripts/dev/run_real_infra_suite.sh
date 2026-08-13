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
# This is suite 3 of four. `tests/conftest.py` classifies every collected test
# as exactly one of `unit`, `integration`, `live_infra` or `browser`, and
# `pyproject.toml` deselects the last two from the default run -- so the tests
# that need what this container has are the ones that do not run anywhere else.
# The selection below is what fetches them back.
#
#   suite 1+2  bash backend/scripts/dev/run_normal_suite.sh   (host, no infra)
#   suite 3    this script
#   suite 4    cd frontend && npm run test:e2e
#
# REAL_INFRA_SELECTOR overrides the selection. `REAL_INFRA_SELECTOR='not browser'`
# runs everything in-network, which is worth doing when a host result and a
# container result disagree; it is not the default because the normal suite
# already has a home that does not need Docker.
#
# Usage:  bash scripts/dev/run_real_infra_suite.sh [extra pytest args]
set -euo pipefail

SERVICE="${REAL_INFRA_SERVICE:-diagnostics}"
SELECTOR="${REAL_INFRA_SELECTOR:-live_infra}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
COMPOSE=(docker compose --profile dev-tools)

cd "${REPO_ROOT}"

# `--wait` blocks on the datastore health conditions the service declares, so a
# suite run cannot start against a half-up stack and report infrastructure
# timing as test failures. Idempotent: a no-op when everything is already up.
echo "==> ensuring ${SERVICE} and its dependencies are up"
"${COMPOSE[@]}" up -d --wait "${SERVICE}"

# Excluded, and why:
#   test_ai_model_probe_evaluator   \
#   gate_tools/                      | need repo_root/scripts/, which the
#   test_runtime_env_key_sync.py    /  backend-only bind mount omits.
#
# `--ignore` rather than deselection because the three are missing a *file*:
# they fail at import, which `-m` never gets the chance to prevent. None of them
# is a live-infrastructure test, so nothing is lost by keeping them out of this
# suite -- they run in the normal one.
#
# `test_order_agent_rest.py` used to be a fourth entry here, ignored by name
# because its 40 scenarios each need a real model response. It now says that
# itself: the module is marked `live_infra` and takes `live_ai_credentials`, so
# it runs here when real provider keys are exported and reports a skip with a
# reason when they are not. An exclusion a test declares is one that stops
# applying the moment it is satisfied; one written here never does.
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
echo "==> running suite (-m '${SELECTOR}')"
MSYS_NO_PATHCONV=1 "${COMPOSE[@]}" exec -T "${SERVICE}" /opt/venv/bin/python -m pytest tests/ \
  -p no:cacheprovider \
  -m "${SELECTOR}" \
  --ignore=tests/test_ai_model_probe_evaluator.py \
  --ignore=tests/gate_tools \
  --ignore=tests/test_runtime_env_key_sync.py \
  -q "$@"

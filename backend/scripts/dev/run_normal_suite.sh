#!/usr/bin/env bash
# The host-side suite runner. Default: the normal suite, which is suites 1 and 2.
#
# There are four suites. `tests/conftest.py` gives every collected test exactly
# one of `unit`, `integration`, `live_infra` or `browser`, and `pyproject.toml`
# deselects the last two from the default run:
#
#   suite 1  unit         no infrastructure, no network, no app composition
#   suite 2  integration  composes the app in process; stubs permitted
#   suite 3  live_infra   real Mongo/Neo4j/Temporal/SQL Server/Valkey
#   suite 4  browser      drives a real browser
#
#   suite 1     bash backend/scripts/dev/run_normal_suite.sh unit
#   suite 2     bash backend/scripts/dev/run_normal_suite.sh integration
#   suite 1+2   bash backend/scripts/dev/run_normal_suite.sh          <- the default
#   suite 3     bash backend/scripts/dev/run_normal_suite.sh live_infra
#               bash backend/scripts/dev/run_real_infra_suite.sh      <- same suite, in-network
#   suite 4     cd frontend && npm run test:e2e
#
# Suite 3 runs here. Compose publishes every datastore on the host -- Mongo
# 27017, Neo4j 7687, Temporal 7233, SQL Server 14330 -- and `test_settings`
# defaults to `localhost` with `directConnection=true`, which is what makes the
# single-node replica set reachable from outside its own network. The container
# route exists for the cases the host cannot answer: an in-network address, a
# Linux-only driver path, or a disagreement between the two that has to be
# settled.
#
# The frontend's own unit suite (`cd frontend && npm run test`) belongs to
# suite 1 and runs under Vitest, which has its own runner; the `browser` marker
# exists on the Python side so that a Playwright-driving backend test has a
# suite to be in rather than a reason to be skipped.
#
# Why this exists as a script rather than as three lines in a README: every one
# of these has been got wrong, silently, and each mistake produced a result that
# looked like a verdict.
#
#   PYTHONPATH  the venv has `return_platform` installed editable against
#               whichever checkout it was created in. From a worktree, without
#               this, you test the other tree and never find out.
#   .env        `tests/conftest.py::pytest_configure` raises without one at the
#               repository root. Deliberate, and it must stay that way -- the
#               fixture reads real secrets from it.
#   AI keys     `Settings` needs the gateway fields populated for a route pool
#               to exist. Placeholders satisfy that; nothing outside suite 3
#               dispatches to a provider, and a test that does takes
#               `live_ai_credentials` and skips without real ones. Never write a
#               key into `.env`.
#
# Exported placeholders are for those two variables and *nothing else*. It is
# tempting to export GRAPH_PASSWORD, VALKEY_PASSWORD, MSSQL_SA_PASSWORD,
# MONGO_ROOT_USERNAME and MONGO_ROOT_PASSWORD alongside them, and it is exactly
# backwards: `pytest_configure` loads `.env` with `override=False`, so an
# exported placeholder *wins* over the real credential in the file. Doing that
# produced nine Mongo authentication failures and tripped Neo4j's
# `AuthenticationRateLimit`, which then failed every Neo4j test until the
# container was restarted. Those five come from `.env` or they do not come.
#
# Usage:  bash scripts/dev/run_normal_suite.sh [unit|integration|live_infra|all] [pytest args]
set -euo pipefail

BACKEND_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO_ROOT="$(cd "${BACKEND_ROOT}/.." && pwd)"

SELECTOR="not live_infra and not browser"
case "${1:-}" in
  unit | integration | live_infra | browser)
    SELECTOR="$1"
    shift
    ;;
  all)
    SELECTOR="not browser"
    shift
    ;;
esac

if [ ! -f "${REPO_ROOT}/.env" ]; then
  echo "no ${REPO_ROOT}/.env -- copy the one from the main checkout (it is gitignored)" >&2
  exit 1
fi

# A git worktree has no venv of its own -- the interpreter lives in the checkout
# it was created from, which is not derivable from here. That is what
# PLATFORM_TEST_PYTHON is for, and why the PATH interpreter is tried before
# giving up: PYTHONPATH above already points the import at *this* tree, so any
# interpreter with the dev group installed tests the right code.
PYTHON="${PLATFORM_TEST_PYTHON:-}"
if [ -z "${PYTHON}" ]; then
  for candidate in "${BACKEND_ROOT}/.venv/Scripts/python.exe" \
                   "${BACKEND_ROOT}/.venv/bin/python" \
                   "${REPO_ROOT}/backend/.venv/Scripts/python.exe" \
                   "${REPO_ROOT}/backend/.venv/bin/python"; do
    if [ -x "${candidate}" ]; then
      PYTHON="${candidate}"
      break
    fi
  done
fi
if [ -z "${PYTHON}" ] && command -v python >/dev/null 2>&1; then
  if python -c "import pytest" >/dev/null 2>&1; then
    PYTHON="$(command -v python)"
  fi
fi
if [ -z "${PYTHON}" ]; then
  echo "no interpreter with pytest found." >&2
  echo "set PLATFORM_TEST_PYTHON to one with the dev group installed -- from a" >&2
  echo "worktree that is the venv in the checkout the worktree was made from." >&2
  exit 1
fi

# Ahead of the editable install, not behind it -- see the note above.
export PYTHONPATH="${BACKEND_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

# Only when the real ones are absent, so a machine that has credentials keeps
# them and `live_ai_credentials` still opts in correctly.
export NVIDIA_API_KEY="${NVIDIA_API_KEY:-placeholder-not-a-real-key}"
export GOOGLE_API_KEY="${GOOGLE_API_KEY:-placeholder-not-a-real-key}"

cd "${BACKEND_ROOT}"

echo "==> running -m '${SELECTOR}'"
exec "${PYTHON}" -m pytest tests/ -q -m "${SELECTOR}" "$@"

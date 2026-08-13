#!/usr/bin/env bash
# The canonical normal-suite invocation: suites 1 and 2, on this machine, with
# no Docker and no datastore.
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
#   suite 3     bash backend/scripts/dev/run_real_infra_suite.sh
#   suite 4     cd frontend && npm run test:e2e
#
# The frontend's own unit suite (`cd frontend && npm run test`) belongs to
# suite 1 and runs under Vitest, which has its own runner; the `browser` marker
# exists on the Python side so that a Playwright-driving backend test has a
# suite to be in rather than a reason to be skipped.
#
# Why this exists as a script rather than as three lines in a README: every one
# of those three lines has been got wrong, silently, and each mistake produced a
# result that looked fine.
#
#   PYTHONPATH  the venv has `return_platform` installed editable against
#               whichever checkout it was created in. From a worktree, without
#               this, you test the other tree and never find out.
#   .env        `tests/conftest.py::pytest_configure` raises without one at the
#               repository root. Deliberate, and it must stay that way -- the
#               fixture reads real secrets from it.
#   AI keys     `Settings` needs the gateway fields populated for a route pool
#               to exist. Placeholders satisfy that; nothing in these two suites
#               dispatches to a provider, and a test that would takes
#               `live_ai_credentials` and skips without real ones. Never write a
#               key into `.env`.
#
# Usage:  bash scripts/dev/run_normal_suite.sh [unit|integration] [extra pytest args]
set -euo pipefail

BACKEND_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO_ROOT="$(cd "${BACKEND_ROOT}/.." && pwd)"

SELECTOR="not live_infra and not browser"
if [ "${1:-}" = "unit" ] || [ "${1:-}" = "integration" ]; then
  SELECTOR="$1"
  shift
fi

if [ ! -f "${REPO_ROOT}/.env" ]; then
  echo "no ${REPO_ROOT}/.env -- copy the one from the main checkout (it is gitignored)" >&2
  exit 1
fi

PYTHON="${PLATFORM_TEST_PYTHON:-}"
if [ -z "${PYTHON}" ]; then
  for candidate in "${BACKEND_ROOT}/.venv/Scripts/python.exe" \
                   "${BACKEND_ROOT}/.venv/bin/python" \
                   "${REPO_ROOT}/.venv/bin/python"; do
    if [ -x "${candidate}" ]; then
      PYTHON="${candidate}"
      break
    fi
  done
fi
if [ -z "${PYTHON}" ]; then
  echo "no interpreter found; set PLATFORM_TEST_PYTHON to one with the dev group installed" >&2
  exit 1
fi

# Ahead of the editable install, not behind it -- see the note above.
export PYTHONPATH="${BACKEND_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

# Only when the real ones are absent, so a machine that has credentials keeps
# them and `live_ai_credentials` still opts in correctly.
export NVIDIA_API_KEY="${NVIDIA_API_KEY:-placeholder-not-a-real-key}"
export GOOGLE_API_KEY="${GOOGLE_API_KEY:-placeholder-not-a-real-key}"

cd "${BACKEND_ROOT}"

echo "==> running normal suite (-m '${SELECTOR}')"
exec "${PYTHON}" -m pytest tests/ -q -m "${SELECTOR}" "$@"

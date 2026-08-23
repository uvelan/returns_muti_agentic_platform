#!/usr/bin/env bash
# The live-infrastructure suite: the 496 tests the default run deselects.
#
# `backend/pyproject.toml` has named this script as the way to run them since the
# marker was introduced, and the script did not exist. So the partition the
# marker documents -- "deselected from the default run, run by this instead" --
# was only half true: the deselection was real and the selection had no entry
# point, which is how 496 mandatory tests end up with nobody running them.
#
# What it does that `pytest -m live_infra` alone does not:
#
#   * refuses to run against absent datastores, so a connection error reads as
#     "start the stack" rather than as 496 failures;
#   * reports the collected total, because "the live suite passed" means nothing
#     without the number it passed out of -- a marker typo that collects zero
#     tests exits 0 and looks identical to success;
#   * passes through extra arguments, so a single test can be run the same way
#     CI runs the whole file.
#
# Usage:
#   scripts/dev/run_real_infra_suite.sh
#   scripts/dev/run_real_infra_suite.sh tests/operations/test_case_aggregate_real_infra.py
#   scripts/dev/run_real_infra_suite.sh -k reservations -x
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT/backend"

# The interpreter, in the order a developer actually has one. `.venv` first
# because that is what `bootstrap_host` creates and what every other script
# here uses; falling back to whatever `python` resolves to keeps the script
# usable on a CI image that installed into the system environment.
if [[ -x ".venv/Scripts/python.exe" ]]; then
  PYTHON=".venv/Scripts/python.exe"        # Windows / Git Bash
elif [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"                # Linux / macOS
elif command -v python >/dev/null 2>&1; then
  PYTHON="python"
else
  echo "error: no Python interpreter found (looked for backend/.venv, then PATH)" >&2
  exit 1
fi

# Preflight, and the reason it is here: these tests open real drivers. Without
# this check a stopped stack produces hundreds of connection errors that look
# like the code is broken, and the one line that matters -- "the database is not
# running" -- is buried under them.
#
# Ports rather than container names, because the suite reaches the datastores
# over the host ports `.env` names, and a container that is up but unmapped is
# exactly the failure ENV-ACTION-01 recorded during the audit: Temporal running,
# healthy, and publishing no host port at all.
declare -a required_ports=(
  "MongoDB:27017"
  "Neo4j:17687"
  "SQL Server:14330"
  "Valkey:6379"
  # 17233: Windows reserves TCP 7147-7246, so the host publish moved. In-container
  # addressing is still `temporal:7233`.
  "Temporal:17233"
)

missing=()
for entry in "${required_ports[@]}"; do
  name="${entry%%:*}"
  port="${entry##*:}"
  if ! "$PYTHON" -c "
import socket, sys
s = socket.socket()
s.settimeout(2)
sys.exit(0 if s.connect_ex(('127.0.0.1', $port)) == 0 else 1)
" 2>/dev/null; then
    missing+=("$name (127.0.0.1:$port)")
  fi
done

if (( ${#missing[@]} > 0 )); then
  echo "error: the live-infrastructure suite needs datastores that are not reachable:" >&2
  for entry in "${missing[@]}"; do
    echo "  - $entry" >&2
  done
  echo >&2
  echo "Start them with:  scripts/infra.sh start" >&2
  echo "Or check mappings with:  docker compose ps" >&2
  echo >&2
  echo "Not run. This is a missing stack, not a test failure." >&2
  exit 2
fi

# `-m live_infra` replaces the `-m` in `addopts`: it is a store option and the
# command line is parsed after addopts, which is precisely how this suite
# selects itself back in. See the comment on `addopts` in pyproject.toml.
echo "live-infrastructure suite: all five datastores reachable"
collected="$("$PYTHON" -m pytest -m live_infra --collect-only -q "$@" 2>/dev/null | tail -1 || true)"
echo "collection: ${collected:-unknown}"
echo

exec "$PYTHON" -m pytest -m live_infra "$@"

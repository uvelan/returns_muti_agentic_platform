#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
cd "$REPO_ROOT/backend"
# Genuinely needs the Poetry binary, not just the environment: `poetry check`
# validates the lockfile against pyproject.toml, which no `python -m` can do.
poetry_cmd
POETRY=("${POETRY_CMD[@]}")
"${POETRY[@]}" check
"${POETRY[@]}" run ruff check .
"${POETRY[@]}" run ruff format --check .
"${POETRY[@]}" run ruff check \
  "$REPO_ROOT/scripts/probe_configured_ai_models.py" \
  "$REPO_ROOT/scripts/linux/validate_env.py" \
  "$REPO_ROOT/scripts/tests"
"${POETRY[@]}" run ruff format --check \
  "$REPO_ROOT/scripts/probe_configured_ai_models.py" \
  "$REPO_ROOT/scripts/linux/validate_env.py" \
  "$REPO_ROOT/scripts/tests"
"${POETRY[@]}" run mypy src tests
"${POETRY[@]}" run mypy \
  "$REPO_ROOT/scripts/probe_configured_ai_models.py" \
  "$REPO_ROOT/scripts/linux/validate_env.py"
"${POETRY[@]}" run pytest
PYTHONPATH="$REPO_ROOT/backend/src${PYTHONPATH:+:$PYTHONPATH}" \
  "${POETRY[@]}" run pytest "$REPO_ROOT/scripts/tests"

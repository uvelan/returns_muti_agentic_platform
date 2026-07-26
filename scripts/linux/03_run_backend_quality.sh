#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
cd "$REPO_ROOT/backend"
if command -v poetry >/dev/null 2>&1; then
  POETRY=(poetry)
elif [[ -x "$RUNTIME_ROOT/tooling/bin/poetry" ]]; then
  POETRY=("$RUNTIME_ROOT/tooling/bin/poetry")
else
  echo "Poetry is required for locked backend validation." >&2
  exit 2
fi
"${POETRY[@]}" check
"${POETRY[@]}" run ruff check .
"${POETRY[@]}" run ruff format --check .
"${POETRY[@]}" run mypy src tests
"${POETRY[@]}" run pytest

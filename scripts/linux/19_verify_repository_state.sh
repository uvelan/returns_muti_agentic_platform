#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
git -C "$REPO_ROOT" diff --check
git -C "$REPO_ROOT" status --short >"$EVIDENCE_DIR/final-git-status.txt"
[[ ! -s "$EVIDENCE_DIR/final-git-status.txt" ]] || {
  echo "Repository is not clean after validation." >&2
  cat "$EVIDENCE_DIR/final-git-status.txt" >&2
  exit 1
}
repo_fingerprint >"$EVIDENCE_DIR/final-tree-fingerprint.sha256"

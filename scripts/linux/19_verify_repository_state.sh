#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
git -C "$REPO_ROOT" diff --check
git -C "$REPO_ROOT" status --short >"$EVIDENCE_DIR/final-git-status.txt"
repo_fingerprint >"$EVIDENCE_DIR/final-tree-fingerprint.sha256"

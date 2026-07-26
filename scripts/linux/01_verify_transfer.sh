#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
require_command git
require_command sha256sum
git -C "$REPO_ROOT" diff --check
git -C "$REPO_ROOT" rev-parse --verify HEAD
if [[ -f "$REPO_ROOT/docs/evidence/code_quality/windows_to_linux_transfer.json" ]]; then
  readarray -t transfer < <(
    python3 - "$REPO_ROOT/docs/evidence/code_quality/windows_to_linux_transfer.json" <<'PY'
import json
import pathlib
import sys

value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if value.get("status") != "READY_FOR_LINUX":
    raise SystemExit("Transfer manifest is not ready for Linux.")
print(value["baselineCommit"])
print(value["patchPath"])
print(value["patchSha256"])
PY
  )
  [[ "$(git -C "$REPO_ROOT" rev-parse HEAD)" == "${transfer[0]}" ]] || {
    echo "Baseline commit does not match the transfer manifest." >&2
    exit 1
  }
  patch="$REPO_ROOT/${transfer[1]}"
  [[ -f "$patch" ]] || {
    echo "Reviewed patch is missing: $patch" >&2
    exit 1
  }
  [[ "$(sha256sum "$patch" | awk '{print $1}')" == "${transfer[2]}" ]] || {
    echo "Reviewed patch checksum mismatch." >&2
    exit 1
  }
  git -C "$REPO_ROOT" apply --check --reverse --binary "$patch"
fi
printf 'Validated commit %s with tree fingerprint %s\n' \
  "$(git -C "$REPO_ROOT" rev-parse HEAD)" "$(repo_fingerprint)"

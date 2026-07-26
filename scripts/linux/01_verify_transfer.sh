#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
require_command git
require_command sha256sum
require_command python3
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
schema = int(value.get("schemaVersion", 1))
print(schema)
if schema == 1:
    print("LEGACY_PATCH")
    print(value["baselineCommit"])
    print(value["patchPath"])
    print(value["patchSha256"])
    print("")
elif schema == 2:
    print(value.get("verificationMode", ""))
    print(value.get("requiredAncestorCommitPrefix", value.get("expectedCommitPrefix", "")))
    print("")
    print("")
    print(value.get("requiredBranch", ""))
else:
    raise SystemExit(f"Unsupported transfer manifest schema: {schema}")
PY
  )
  schema="${transfer[0]}"
  mode="${transfer[1]}"
  expected_commit="${transfer[2]}"
  patch_path="${transfer[3]}"
  patch_sha="${transfer[4]}"
  required_branch="${transfer[5]}"
  actual_commit="$(git -C "$REPO_ROOT" rev-parse HEAD)"

  case "$mode" in
    ANCESTOR_COMMITTED_TREE)
      required_commit="$(git -C "$REPO_ROOT" rev-parse --verify "${expected_commit}^{commit}")" || {
        echo "Required ancestor commit prefix $expected_commit is not available locally." >&2
        exit 1
      }
      git -C "$REPO_ROOT" merge-base --is-ancestor "$required_commit" "$actual_commit" || {
        echo "Required Stage 4O commit $required_commit is not an ancestor of HEAD $actual_commit." >&2
        exit 1
      }
      if [[ -n "$required_branch" ]]; then
        actual_branch="$(git -C "$REPO_ROOT" branch --show-current)"
        [[ "$actual_branch" == "$required_branch" ]] || {
          echo "Branch $required_branch is required; found ${actual_branch:-detached HEAD}." >&2
          exit 1
        }
      fi
      [[ -z "$(git -C "$REPO_ROOT" status --porcelain)" ]] || {
        echo "Working tree changes or untracked non-ignored files exist before validation." >&2
        exit 1
      }
      ;;
    LEGACY_PATCH)
      [[ "$actual_commit" == "$expected_commit" ]] || {
        echo "Baseline commit does not match the transfer manifest." >&2
        exit 1
      }
      patch="$REPO_ROOT/$patch_path"
      [[ -f "$patch" ]] || {
        echo "Reviewed patch is missing: $patch" >&2
        exit 1
      }
      [[ "$(sha256sum "$patch" | awk '{print $1}')" == "$patch_sha" ]] || {
        echo "Reviewed patch checksum mismatch." >&2
        exit 1
      }
      git -C "$REPO_ROOT" apply --check --reverse --binary "$patch"
      ;;
    *)
      echo "Unsupported transfer verification mode: $mode (schema $schema)." >&2
      exit 1
      ;;
  esac
fi
printf 'Validated commit %s with tree fingerprint %s\n' \
  "$(git -C "$REPO_ROOT" rev-parse HEAD)" "$(repo_fingerprint)"

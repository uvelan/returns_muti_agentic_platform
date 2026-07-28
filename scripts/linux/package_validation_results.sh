#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
require_command tar
require_command sha256sum
[[ -s "$EVIDENCE_DIR/linux-validation-receipt.json" ]] || {
  echo "Linux receipt is missing; run validation or receipt generation first." >&2
  exit 2
}
timestamp="$(date -u +'%Y%m%dT%H%M%SZ')"
output_dir="$REPO_ROOT/artifacts"
archive="$output_dir/linux-validation-$timestamp.tar.gz"
checksum="$archive.sha256"
mkdir -p "$output_dir"
tar --create --gzip --file "$archive" --directory "$RUNTIME_ROOT" evidence logs state
sha256sum "$archive" >"$checksum"
sha256sum --check "$checksum"
printf '%s\n%s\n' "$archive" "$checksum"

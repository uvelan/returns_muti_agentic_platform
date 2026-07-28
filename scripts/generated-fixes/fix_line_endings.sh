#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mapfile -t affected < <(find "$ROOT/scripts/linux" "$ROOT/scripts/generated-fixes" \
  -type f -name '*.sh' -print0 | xargs -0 grep -Il $'\r')
if ((${#affected[@]} == 0)); then
  echo "No CRLF shell scripts were found; no change is required."
  exit 0
fi
backup="$ROOT/.runtime/generated-fix-backups/line-endings-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup"
printf 'CRLF shell scripts:\n'
printf '  %s\n' "${affected[@]}"
read -r -p "Normalize only these files to LF? [y/N] " answer
[[ "$answer" =~ ^[Yy]$ ]] || exit 2
for path in "${affected[@]}"; do
  relative="${path#"$ROOT/"}"
  mkdir -p "$backup/$(dirname "$relative")"
  cp --preserve=mode,timestamps "$path" "$backup/$relative"
  sed -i 's/\r$//' "$path"
done
git -C "$ROOT" diff --check
git -C "$ROOT" diff -- "${affected[@]}"

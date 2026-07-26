#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mapfile -t affected < <(find "$ROOT/scripts/linux" "$ROOT/scripts/generated-fixes" \
  -type f -name '*.sh' ! -perm -u+x -print)
if ((${#affected[@]} == 0)); then
  echo "All validation shell scripts are executable; no change is required."
  exit 0
fi
backup="$ROOT/.runtime/generated-fix-backups/permissions-$(date -u +%Y%m%dT%H%M%SZ).tsv"
mkdir -p "$(dirname "$backup")"
stat --printf='%a\t%n\n' "${affected[@]}" >"$backup"
printf 'Non-executable scripts:\n'
printf '  %s\n' "${affected[@]}"
read -r -p "Add the user executable bit to only these files? [y/N] " answer
[[ "$answer" =~ ^[Yy]$ ]] || exit 2
chmod u+x "${affected[@]}"
stat --printf='%A\t%n\n' "${affected[@]}"
for path in "${affected[@]}"; do
  bash -n "$path"
done

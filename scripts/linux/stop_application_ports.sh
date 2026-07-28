#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

ports=("$@")
if ((${#ports[@]} == 0)); then
  ports=(8000 5173)
fi

if command -v fuser >/dev/null 2>&1; then
  port_tool=fuser
elif command -v lsof >/dev/null 2>&1; then
  port_tool=lsof
else
  echo "Either fuser or lsof is required to clear application ports." >&2
  exit 2
fi

port_pids() {
  local port="$1"
  if [[ "$port_tool" == fuser ]]; then
    fuser -n tcp "$port" 2>/dev/null || true
    return
  fi
  lsof -n -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
}

belongs_to_repository() {
  local pid="$1" process_root
  process_root="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
  [[ "$process_root" == "$REPO_ROOT" || "$process_root" == "$REPO_ROOT/"* ]]
}

for port in "${ports[@]}"; do
  pids=()
  read -r -a pids <<<"$(port_pids "$port")"
  for pid in "${pids[@]:-}"; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    kill -0 "$pid" 2>/dev/null || continue
    if ! belongs_to_repository "$pid"; then
      command_line="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)"
      printf 'Refusing to stop unrelated PID %s on port %s: %s\n' \
        "$pid" "$port" "$command_line" >&2
      exit 1
    fi
    printf 'Stopping repository process PID %s on port %s.\n' "$pid" "$port"
    kill "$pid"
  done
done

for attempt in {1..20}; do
  busy=false
  for port in "${ports[@]}"; do
    if [[ -n "$(port_pids "$port")" ]]; then
      busy=true
    fi
  done
  [[ "$busy" == false ]] && exit 0
  sleep 0.5
done

echo "Application ports did not close within 10 seconds." >&2
exit 1

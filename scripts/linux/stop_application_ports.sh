#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

check_only=false
if [[ "${1:-}" == "--check-only" ]]; then
  check_only=true
  shift
fi

ports=("$@")
if ((${#ports[@]} == 0)); then
  ports=(8000 5173)
fi

for port in "${ports[@]}"; do
  if [[ ! "$port" =~ ^[0-9]+$ ]] || ((port < 1 || port > 65535)); then
    printf 'Invalid TCP port: %s\n' "$port" >&2
    exit 2
  fi
done

if command -v ss >/dev/null 2>&1; then
  listen_tool=ss
elif command -v fuser >/dev/null 2>&1; then
  listen_tool=fuser
elif command -v lsof >/dev/null 2>&1; then
  listen_tool=lsof
else
  echo "One of ss, fuser, or lsof is required to inspect application ports." >&2
  exit 2
fi

if command -v fuser >/dev/null 2>&1; then
  port_tool=fuser
elif command -v lsof >/dev/null 2>&1; then
  port_tool=lsof
else
  port_tool=
fi

port_pids() {
  local port="$1"
  if [[ "$port_tool" == fuser ]]; then
    fuser -n tcp "$port" 2>/dev/null || true
    return
  fi
  lsof -n -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
}

port_is_listening() {
  local port="$1"
  if [[ "$listen_tool" == ss ]]; then
    [[ -n "$(ss -H -ltn "sport = :$port" 2>/dev/null)" ]]
    return
  fi
  [[ -n "$(port_pids "$port")" ]]
}

print_port_diagnostic() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -H -ltnp "sport = :$port" 2>/dev/null || true
  elif command -v lsof >/dev/null 2>&1; then
    lsof -n -P -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
  else
    fuser -v -n tcp "$port" 2>/dev/null || true
  fi
}

assert_ports_closed() {
  local port
  local -a busy_ports=()
  for port in "${ports[@]}"; do
    if port_is_listening "$port"; then
      busy_ports+=("$port")
    fi
  done
  if ((${#busy_ports[@]} > 0)); then
    printf 'TCP ports still listening: %s\n' "${busy_ports[*]}" >&2
    for port in "${busy_ports[@]}"; do
      print_port_diagnostic "$port" >&2
    done
    return 1
  fi
  printf 'Verified TCP ports are closed: %s\n' "${ports[*]}"
}

belongs_to_repository() {
  local pid="$1" process_root
  process_root="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
  [[ "$process_root" == "$REPO_ROOT" || "$process_root" == "$REPO_ROOT/"* ]]
}

if [[ "$check_only" == true ]]; then
  assert_ports_closed
  exit
fi

if [[ -z "$port_tool" ]]; then
  echo "Either fuser or lsof is required to stop application port owners." >&2
  exit 2
fi

for port in "${ports[@]}"; do
  pids=()
  mapfile -t pids < <(
    port_pids "$port" |
      tr ' ' '\n' |
      awk '/^[0-9]+$/ && !seen[$0]++'
  )
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
  if assert_ports_closed >/dev/null 2>&1; then
    assert_ports_closed
    exit 0
  fi
  sleep 0.5
done

echo "Application ports did not close within 10 seconds." >&2
assert_ports_closed || true
exit 1

#!/bin/sh
# What this host can and cannot do, before anything tries to run on it.
#
# Every other script here is a gate: it checks a precondition and exits 2 on the
# first one that fails. On a locked-down host that is the wrong shape entirely --
# you fix one thing, rerun, and learn about the next one, ten times over. This
# reports the whole picture once, and never exits early.
#
# It is written to survive the restrictions it is looking for:
#
#   * POSIX sh, not bash. `#!/usr/bin/env bash` is itself a thing that can be
#     missing, and this script has to run before you know whether it is.
#   * No `set -e`. A probe that fails is a finding to print, not a reason to
#     stop.
#   * No Python, no jq, no docker, no network. Each is probed for and used only
#     if it answered.
#   * No writes required. It writes a copy of the report only where it already
#     has permission, and says so.
#
# Secrets are never printed. `.env` is read for key NAMES and whether a value is
# present; values are reported only as "set (N chars)".
#
# Usage:
#   sh scripts/linux/environment_report.sh              # human report
#   sh scripts/linux/environment_report.sh --json       # machine-readable too
#   sh scripts/linux/environment_report.sh --strict     # exit 1 if blocked
#   sh scripts/linux/environment_report.sh --quick      # skip network probes

WANT_JSON=0
STRICT=0
QUICK=0
for arg in "$@"; do
  case "$arg" in
    --json) WANT_JSON=1 ;;
    --strict) STRICT=1 ;;
    --quick) QUICK=1 ;;
    -h|--help) sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$arg" >&2; exit 64 ;;
  esac
done

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

BLOCKERS=""
WARNINGS=""
JSON_ROWS=""
OK_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
INFO_COUNT=0

# ---------------------------------------------------------------------------
# Reporting primitives.
#
# One line per finding, in a fixed three-column shape so the whole report can be
# skimmed for the word FAIL. `note` is for facts with no verdict attached --
# a version string, a path -- which are the ones you end up quoting to whoever
# administers the host.
# ---------------------------------------------------------------------------

json_escape() {
  # Backslash and double quote are the only characters that must be escaped for
  # the values this report emits; control characters are removed rather than
  # escaped, since every value here is a version string, a path or a sentence.
  printf '%s' "$1" \
    | tr -d '\000-\037' \
    | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

flatten() {
  # Some probes answer over several lines (id -Gn, docker info). One finding is
  # one line, or the report stops being skimmable.
  printf '%s' "$1" | tr '
	' '   ' | sed -e 's/  */ /g' -e 's/^ //' -e 's/ $//'
}

record() {
  # Only pay for JSON when JSON was asked for.
  [ "$WANT_JSON" -eq 1 ] || return 0
  JSON_ROWS="${JSON_ROWS}    {\"status\": \"$(json_escape "$1")\", \"check\": \"$(json_escape "$2")\", \"detail\": \"$(json_escape "$3")\"},
"
}

ok() {
  OK_COUNT=$((OK_COUNT + 1))
  detail=$(flatten "$2")
  printf '  [ ok ] %-34s %s\n' "$1" "$detail"
  record ok "$1" "$detail"
}
note() {
  INFO_COUNT=$((INFO_COUNT + 1))
  detail=$(flatten "$2")
  printf '  [ .. ] %-34s %s\n' "$1" "$detail"
  record info "$1" "$detail"
}
warn() {
  WARN_COUNT=$((WARN_COUNT + 1))
  detail=$(flatten "$2")
  printf '  [warn] %-34s %s\n' "$1" "$detail"
  record warn "$1" "$detail"
  WARNINGS="${WARNINGS}  - $1: $detail
"
}
fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  detail=$(flatten "$2")
  printf '  [FAIL] %-34s %s\n' "$1" "$detail"
  record fail "$1" "$detail"
  BLOCKERS="${BLOCKERS}  - $1: $detail
"
}

section() { printf '\n%s\n' "$1"; printf '%s\n' "-------------------------------------------------------------------------------"; }

have() { command -v "$1" >/dev/null 2>&1; }
first_line() { head -n 1 2>/dev/null; }

# Run a command, capture stdout+stderr, never let a failure escape.
try() { "$@" 2>&1; }

writable_dir() {
  # A -w test lies on read-only mounts and on some overlay/NFS setups, so write.
  probe="$1/.write-probe.$$"
  if (: >"$probe") 2>/dev/null; then
    rm -f "$probe" 2>/dev/null
    return 0
  fi
  return 1
}

executable_dir() {
  # noexec on /tmp is common on hardened hosts and breaks npm, poetry installers
  # and anything that unpacks a binary before running it.
  probe="$1/.exec-probe.$$"
  if (printf '#!/bin/sh\nexit 7\n' >"$probe") 2>/dev/null; then
    chmod +x "$probe" 2>/dev/null
    "$probe" >/dev/null 2>&1
    status=$?
    rm -f "$probe" 2>/dev/null
    [ "$status" -eq 7 ] && return 0
    return 1
  fi
  return 2
}

port_state() {
  # "listening" / "free" / "unknown", without needing root or ss.
  port="$1"
  # Match the port as a field rather than by column number: ss, GNU netstat
  # and BSD netstat each put the local address somewhere different, and a
  # column index that is right on one is silently wrong on the others.
  if have ss; then
    if ss -ltn 2>/dev/null | grep -qE "[:.]${port}[[:space:]]"; then
      printf 'listening'; return
    fi
    printf 'free'; return
  fi
  if have netstat; then
    # `-ltn` is GNU. Where it is not understood the command prints its usage to
    # stdout and every port then reads as free, which is the worst possible
    # answer -- so fall back to `-an`, which every netstat has.
    listeners=$(netstat -ltn 2>/dev/null)
    printf '%s' "$listeners" | grep -qi 'listen' || listeners=$(netstat -an 2>/dev/null)
    if printf '%s' "$listeners" | grep -E "[:.]${port}[[:space:]]" | grep -qi 'listen'; then
      printf 'listening'; return
    fi
    printf 'free'; return
  fi
  if [ -r /proc/net/tcp ]; then
    hex=$(printf '%04X' "$port" 2>/dev/null)
    if awk 'NR>1 {print $2, $4}' /proc/net/tcp 2>/dev/null | grep -qi ":${hex} 0A"; then
      printf 'listening'; return
    fi
    printf 'free'; return
  fi
  printf 'unknown'
}

tcp_reachable() {
  # host port -> 0 if something answers. Tries the tools a restricted host is
  # most likely to still have, in order of how quiet they are.
  host="$1"; port="$2"
  if have nc; then
    nc -z -w 2 "$host" "$port" >/dev/null 2>&1 && return 0
    return 1
  fi
  if have curl; then
    curl -s -o /dev/null --connect-timeout 2 "telnet://${host}:${port}" >/dev/null 2>&1 && return 0
    return 1
  fi
  if [ -n "$BASH_BIN" ]; then
    "$BASH_BIN" -c "exec 3<>/dev/tcp/${host}/${port}" >/dev/null 2>&1 && return 0
    return 1
  fi
  return 2
}

printf '===============================================================================\n'
printf 'RETURNS PLATFORM -- LINUX ENVIRONMENT REPORT\n'
printf 'generated %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date)"
printf 'repository %s\n' "$REPO_ROOT"
printf '===============================================================================\n'

# ---------------------------------------------------------------------------
section 'HOST AND SHELL'
# ---------------------------------------------------------------------------

note 'kernel' "$(uname -srm 2>/dev/null || printf 'unknown')"
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  distro=$(. /etc/os-release 2>/dev/null; printf '%s %s' "${NAME:-?}" "${VERSION:-${VERSION_ID:-}}")
  note 'distribution' "$distro"
else
  warn 'distribution' '/etc/os-release is unreadable; this may be a minimal or hardened image'
fi
note 'architecture' "$(uname -m 2>/dev/null || printf 'unknown')"
note 'hostname' "$(hostname 2>/dev/null || cat /etc/hostname 2>/dev/null || printf 'unknown')"
note 'current shell' "${SHELL:-unset} (this script is running under $( (readlink /proc/$$/exe 2>/dev/null) || printf 'sh'))"

BASH_BIN=$(command -v bash 2>/dev/null)
if [ -n "$BASH_BIN" ]; then
  bash_version=$("$BASH_BIN" -c 'printf "%s" "$BASH_VERSION"' 2>/dev/null)
  bash_major=$(printf '%s' "$bash_version" | cut -d. -f1)
  if [ "${bash_major:-0}" -ge 4 ] 2>/dev/null; then
    ok 'bash' "$bash_version at $BASH_BIN"
  else
    fail 'bash' "version $bash_version is too old; scripts/linux/*.sh use mapfile and [[ ]], which need bash 4+"
  fi
else
  fail 'bash' 'not installed -- every scripts/linux/NN_*.sh starts with #!/usr/bin/env bash and cannot run'
fi

# The container question changes what "restricted" means: inside a container you
# usually cannot start sibling containers, and the compose path is unavailable
# unless the docker socket is mounted.
if [ -f /.dockerenv ] || [ -f /run/.containerenv ]; then
  note 'containerised' 'yes (/.dockerenv or /run/.containerenv present)'
elif grep -qE '(docker|containerd|kubepods|lxc)' /proc/1/cgroup 2>/dev/null; then
  note 'containerised' 'yes (per /proc/1/cgroup)'
elif have systemd-detect-virt; then
  note 'containerised' "$(systemd-detect-virt 2>/dev/null || printf 'none')"
else
  note 'containerised' 'no evidence found'
fi

if have nproc; then note 'cpus' "$(nproc 2>/dev/null)"; fi
if [ -r /proc/meminfo ]; then
  mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null)
  mem_avail=$(awk '/MemAvailable/ {print $2}' /proc/meminfo 2>/dev/null)
  if [ -n "$mem_kb" ]; then
    total_gb=$((mem_kb / 1024 / 1024))
    avail_gb=$((${mem_avail:-0} / 1024 / 1024))
    if [ "$total_gb" -lt 8 ]; then
      warn 'memory' "${total_gb}GB total, ${avail_gb}GB available -- SQL Server alone asks for 2GB and the full stack is tight under 8GB"
    else
      ok 'memory' "${total_gb}GB total, ${avail_gb}GB available"
    fi
  fi
fi

# ---------------------------------------------------------------------------
section 'IDENTITY AND PRIVILEGE'
# ---------------------------------------------------------------------------

note 'user' "$( (id -un 2>/dev/null) || printf "${USER:-unknown}") (uid $( (id -u 2>/dev/null) || printf '?'), gid $( (id -g 2>/dev/null) || printf '?'))"
note 'groups' "$( (id -Gn 2>/dev/null) || printf 'unknown')"
note 'home' "${HOME:-unset}"
note 'umask' "$(umask 2>/dev/null)"

if [ "$( (id -u 2>/dev/null) || printf 1000)" = "0" ]; then
  note 'root' 'running as root'
else
  if have sudo && sudo -n true >/dev/null 2>&1; then
    note 'sudo' 'available without a password'
  elif have sudo; then
    note 'sudo' 'installed but needs a password (non-interactive scripts cannot use it)'
  else
    note 'sudo' 'not installed -- nothing here may install system packages'
  fi
fi

if have getenforce; then
  enforce=$(getenforce 2>/dev/null)
  case "$enforce" in
    Enforcing) warn 'selinux' 'Enforcing -- bind mounts into containers need the :z/:Z flag or they read as empty' ;;
    *) note 'selinux' "$enforce" ;;
  esac
elif [ -d /sys/kernel/security/apparmor ]; then
  note 'apparmor' 'loaded'
fi

for limit_name in 'open files:-n' 'processes:-u'; do
  label=${limit_name%%:*}
  flag=${limit_name##*:}
  value=$(ulimit "$flag" 2>/dev/null)
  case "$label:$value" in
    'open files:'*)
      if [ "$value" != "unlimited" ] && [ "${value:-0}" -lt 4096 ] 2>/dev/null; then
        warn "ulimit ($label)" "$value -- Vite and the Node test runner open more than this"
      else
        note "ulimit ($label)" "$value"
      fi
      ;;
    *) note "ulimit ($label)" "$value" ;;
  esac
done

# ---------------------------------------------------------------------------
section 'FILESYSTEM PERMISSIONS'
# ---------------------------------------------------------------------------

for target in "$REPO_ROOT" "$REPO_ROOT/backend" "$REPO_ROOT/frontend" "${HOME:-/nonexistent}" "${TMPDIR:-/tmp}"; do
  label=$(printf '%s' "$target" | sed "s|^$REPO_ROOT|<repo>|")
  if [ ! -d "$target" ]; then
    warn "writable: $label" 'directory does not exist'
  elif writable_dir "$target"; then
    ok "writable: $label" 'yes'
  else
    if [ "$target" = "$REPO_ROOT" ] || [ "$target" = "${TMPDIR:-/tmp}" ]; then
      fail "writable: $label" 'NO -- the build writes here (.venv, node_modules, .runtime, logs)'
    else
      warn "writable: $label" 'no'
    fi
  fi
done

for target in "${TMPDIR:-/tmp}" "$REPO_ROOT"; do
  label=$(printf '%s' "$target" | sed "s|^$REPO_ROOT|<repo>|")
  executable_dir "$target"
  case $? in
    0) ok "exec allowed: $label" 'yes' ;;
    1) fail "exec allowed: $label" 'mounted noexec -- npm postinstall, playwright and poetry installers all execute from here' ;;
    *) warn "exec allowed: $label" 'could not test (not writable)' ;;
  esac
done

for cache in "${HOME:-/nonexistent}/.cache" "${HOME:-/nonexistent}/.local" "${HOME:-/nonexistent}/.npm"; do
  short=$(printf '%s' "$cache" | sed "s|^${HOME:-/nonexistent}|~|")
  if [ -d "$cache" ]; then
    if writable_dir "$cache"; then ok "writable: $short" 'yes'; else warn "writable: $short" 'no -- poetry/npm caches will fail or need redirecting'; fi
  else
    note "writable: $short" 'does not exist yet (will be created on first use)'
  fi
done

if have df; then
  for target in "$REPO_ROOT" "${TMPDIR:-/tmp}" "${HOME:-/tmp}"; do
    [ -d "$target" ] || continue
    avail=$(df -Pk "$target" 2>/dev/null | awk 'NR==2 {print int($4/1024/1024)}')
    label=$(printf '%s' "$target" | sed "s|^$REPO_ROOT|<repo>|")
    if [ -n "$avail" ]; then
      if [ "$avail" -lt 10 ]; then
        if [ "$target" = "${TMPDIR:-/tmp}" ]; then
          warn "free space: $label" "${avail}GB -- pip, Poetry and npm all unpack here, and running out reports a broken package rather than a full disk; point TMPDIR somewhere larger: export TMPDIR=\"\$PWD/.tmp/build\" && mkdir -p \"\$TMPDIR\""
        else
          warn "free space: $label" "${avail}GB -- images, node_modules and .venv need well over 10GB together"
        fi
      else
        note "free space: $label" "${avail}GB"
      fi
    fi
  done
fi

# ---------------------------------------------------------------------------
section 'REQUIRED COMMANDS'
# ---------------------------------------------------------------------------
#
# The list scripts/linux/00_validate_prerequisites.sh enforces, reported all at
# once instead of one exit per rerun.

for tool in git curl tar sha256sum awk sed grep; do
  path=$(command -v "$tool" 2>/dev/null)
  if [ -n "$path" ]; then ok "$tool" "$path"; else fail "$tool" 'missing -- required by the validation phases'; fi
done

for tool in jq flock unzip nc; do
  path=$(command -v "$tool" 2>/dev/null)
  if [ -n "$path" ]; then
    ok "$tool" "$path"
  else
    case "$tool" in
      flock) fail 'flock' 'missing -- scripts/prepare_runtime_configuration.sh takes a lock before every backend start' ;;
      jq)    fail 'jq' 'missing -- the validation phases parse JSON receipts with it' ;;
      *)     warn "$tool" 'missing (used by some phases; not fatal)' ;;
    esac
  fi
done

port_tool=''
for tool in ss lsof fuser netstat; do
  if have "$tool"; then port_tool="$port_tool $tool"; fi
done
if [ -n "$port_tool" ]; then
  ok 'port tools' "$(printf '%s' "$port_tool" | sed 's/^ //')"
else
  warn 'port tools' 'none of ss, lsof, fuser, netstat -- stop_application_ports.sh cannot free 8000/5173'
fi

# ---------------------------------------------------------------------------
section 'PYTHON'
# ---------------------------------------------------------------------------
#
# The backend pins >=3.13,<3.14. Everything below reports what is actually on
# the host rather than asserting the pin, because on a restricted host the
# interpreter is usually the thing you cannot change.

PY_313=''
for candidate in python3.13 python3 python; do
  path=$(command -v "$candidate" 2>/dev/null)
  [ -n "$path" ] || continue
  version=$("$path" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null)
  [ -n "$version" ] || { warn "$candidate" "at $path but will not run"; continue; }
  case "$version" in
    3.13.*) ok "$candidate" "$version at $path"; [ -n "$PY_313" ] || PY_313="$path" ;;
    *)      note "$candidate" "$version at $path" ;;
  esac
done

# pyenv and other user-local installs are how a 3.13 usually arrives on a host
# whose package manager is locked down.
for extra in "${HOME:-/nonexistent}/.pyenv/versions" /usr/local/bin /opt/python; do
  [ -d "$extra" ] || continue
  found=$(ls "$extra" 2>/dev/null | grep -E '^(3\.13|python3\.13)' | first_line)
  [ -n "$found" ] && note 'python (user-local)' "$extra/$found"
done

if [ -z "$PY_313" ]; then
  fail 'python 3.13' 'not found on PATH -- backend/pyproject.toml requires >=3.13,<3.14'
else
  note 'python 3.13 resolved' "$PY_313"
fi

PY_ANY=${PY_313:-$(command -v python3 2>/dev/null || command -v python 2>/dev/null)}

# The reported problem: an interpreter exists, but `venv` does not work. Find
# out which half is missing rather than guessing -- a stripped `python3-venv`
# and a read-only target fail in completely different ways.
if [ -n "$PY_ANY" ]; then
  if "$PY_ANY" -c 'import venv' >/dev/null 2>&1; then
    ok 'python venv module' 'importable'
  else
    fail 'python venv module' "$PY_ANY cannot import venv -- install python3-venv (Debian/Ubuntu) or python3-libs (RHEL)"
  fi
  if "$PY_ANY" -m ensurepip --version >/dev/null 2>&1; then
    ok 'python ensurepip' 'present'
  else
    warn 'python ensurepip' 'missing -- venvs must be created with --without-pip and populated another way'
  fi
  if "$PY_ANY" -m pip --version >/dev/null 2>&1; then
    ok 'pip' "$("$PY_ANY" -m pip --version 2>/dev/null | cut -c1-60)"
  else
    warn 'pip' 'not available to this interpreter'
  fi

  # Actually create one. Every other answer here is inference.
  venv_probe="${TMPDIR:-/tmp}/venv-probe.$$"
  venv_error=$("$PY_ANY" -m venv "$venv_probe" 2>&1)
  venv_status=$?
  if [ -x "$venv_probe/bin/python" ] || [ -x "$venv_probe/bin/python3" ]; then
    ok 'venv creation' 'a virtual environment can be created on this host'
  elif [ -f "$venv_probe/Scripts/python.exe" ]; then
    # Windows layout. Not the target platform, but say what was seen rather
    # than reporting a Linux failure that did not happen.
    note 'venv creation' 'succeeded with a Windows layout (Scripts/, not bin/) -- this host is not Linux'
  else
    reason=$(printf '%s' "$venv_error" | tr '\r\n\t' '   ' | sed 's/  */ /g' | cut -c1-140)
    [ -n "$reason" ] || reason="exit status $venv_status, no message"
    fail 'venv creation' "cannot create one: $reason"
  fi
  rm -rf "$venv_probe" 2>/dev/null
fi

# ---------------------------------------------------------------------------
section 'POETRY AND THE BACKEND ENVIRONMENT'
# ---------------------------------------------------------------------------

POETRY=''
for candidate in \
  "$(command -v poetry 2>/dev/null)" \
  "${POETRY_HOME:-/nonexistent}/bin/poetry" \
  "${HOME:-/nonexistent}/.local/bin/poetry" \
  "$REPO_ROOT/.tmp/poetry/bin/poetry" \
  "$REPO_ROOT/.runtime/linux-validation/tooling/bin/poetry"; do
  [ -n "$candidate" ] || continue
  if [ -x "$candidate" ]; then POETRY="$candidate"; break; fi
done

if [ -n "$POETRY" ]; then
  ok 'poetry' "$("$POETRY" --version 2>/dev/null | first_line) at $POETRY"
  case "$POETRY" in
    "$REPO_ROOT"/*) note 'poetry on PATH' 'no -- it lives in the repo; scripts resolve it through lib/common.sh poetry_cmd()' ;;
  esac
  in_project=$(cd "$REPO_ROOT/backend" 2>/dev/null && "$POETRY" config virtualenvs.in-project 2>/dev/null | first_line)
  create=$(cd "$REPO_ROOT/backend" 2>/dev/null && "$POETRY" config virtualenvs.create 2>/dev/null | first_line)
  note 'poetry virtualenvs.create' "${create:-unknown}"
  case "$in_project" in
    true) ok 'poetry virtualenvs.in-project' 'true -- but this governs CREATION only: an environment that already exists for this project keeps being used and is not moved into backend/.venv' ;;
    '')   warn 'poetry virtualenvs.in-project' 'could not be read' ;;
    *)    warn 'poetry virtualenvs.in-project' "$in_project -- the environment goes to a hashed cache directory that this repo's fallbacks cannot find; fix with: poetry config --local virtualenvs.in-project true" ;;
  esac
  env_path=$(cd "$REPO_ROOT/backend" 2>/dev/null && "$POETRY" env info --path 2>/dev/null | first_line)
  if [ -n "$env_path" ]; then
    note 'poetry env path' "$env_path"
  else
    note 'poetry env path' 'poetry reports no environment for backend/'
  fi
else
  warn 'poetry' 'not found on PATH, in $POETRY_HOME, ~/.local/bin, .tmp/poetry or .runtime tooling'
fi

for var in POETRY_HOME POETRY_CACHE_DIR POETRY_VIRTUALENVS_PATH POETRY_VIRTUALENVS_CREATE POETRY_VIRTUALENVS_IN_PROJECT PIP_INDEX_URL PIP_CACHE_DIR; do
  value=$(eval "printf '%s' \"\${$var:-}\"")
  if [ -n "$value" ]; then note "env: $var" "$value"; fi
done

BACKEND_PY=''
BACKEND_PY_SOURCE=''
# Poetry's own environment counts, and on a host where one already existed it is
# the only one there is: `virtualenvs.in-project` governs CREATION, so a project
# with an environment already in the cache keeps using it and `backend/.venv` is
# never written. That is not a broken host -- `run_backend_host.sh` runs
# `poetry run uvicorn`, and `backend_python()` in lib/common.sh tries
# `poetry run python` before it looks at `.venv` -- so the report has to ask
# Poetry where the environment is rather than concluding there is none.
for candidate in   "$REPO_ROOT/backend/.venv/bin/python"   "$REPO_ROOT/backend/.venv/bin/python3"   "$REPO_ROOT/backend/.venv/Scripts/python.exe"   "${env_path:-/nonexistent}/bin/python"   "${env_path:-/nonexistent}/Scripts/python.exe"; do
  # It must actually run: a .venv built on another host -- or a Windows one
  # seen through a bind mount -- is executable and still not an interpreter.
  if [ -x "$candidate" ] && "$candidate" -c "" >/dev/null 2>&1; then
    BACKEND_PY="$candidate"
    case "$candidate" in
      "$REPO_ROOT"/*) BACKEND_PY_SOURCE='in-project' ;;
      *) BACKEND_PY_SOURCE='poetry-cache' ;;
    esac
    break
  fi
done

if [ "$BACKEND_PY_SOURCE" = 'in-project' ]; then
  ok 'backend/.venv' "python $("$BACKEND_PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null)"
elif [ "$BACKEND_PY_SOURCE" = 'poetry-cache' ]; then
  ok 'backend environment' "python $("$BACKEND_PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null) in Poetry's cache, not backend/.venv -- fine for anything run through poetry, which is every launcher here. Only a script invoked as backend/.venv/bin/python directly would miss it."
elif [ -d "$REPO_ROOT/backend/.venv" ]; then
  fail 'backend/.venv' 'directory exists but holds no usable interpreter -- delete it and recreate with poetry sync'
else
  warn 'backend environment' 'none found, in backend/.venv or in Poetry -- create with: cd backend && poetry config --local virtualenvs.in-project true && poetry env use python3.13 && poetry sync'
fi

# Whether the dependencies are actually importable is the only question that
# decides if the backend starts. Ask whichever environment was found, and the
# system interpreter otherwise -- `virtualenvs.create false` installs into user
# site, and that is a legitimate answer on a host where venvs are unavailable.
IMPORT_PY=${BACKEND_PY:-$PY_ANY}
if [ -n "$IMPORT_PY" ]; then
  missing=''
  for module in fastapi uvicorn pydantic pymongo neo4j temporalio psycopg; do
    "$IMPORT_PY" -c "import $module" >/dev/null 2>&1 || missing="$missing $module"
  done
  if [ -z "$missing" ]; then
    ok 'backend imports' 'fastapi, uvicorn, pydantic, pymongo, neo4j, temporalio, psycopg all import'
  else
    warn 'backend imports' "missing:$missing (run poetry sync in backend/)"
  fi
  # pymssql is a compiled extension against FreeTDS and is the one that fails on
  # a host without the system library, long after `poetry sync` reported success.
  if "$IMPORT_PY" -c 'import pymssql' >/dev/null 2>&1; then
    ok 'pymssql' 'imports (FreeTDS present)'
  else
    detail=$("$IMPORT_PY" -c 'import pymssql' 2>&1 | tail -n 1 | cut -c1-110)
    warn 'pymssql' "does not import: $detail"
  fi
  note 'import interpreter' "$IMPORT_PY"
fi

# ---------------------------------------------------------------------------
section 'NODE AND THE FRONTEND'
# ---------------------------------------------------------------------------
#
# frontend/package.json pins node 24.18.0 and npm 11.16.0.

if have node; then
  node_version=$(node --version 2>/dev/null | sed 's/^v//')
  node_major=${node_version%%.*}
  if [ "${node_major:-0}" -eq 24 ] 2>/dev/null; then
    ok 'node' "$node_version at $(command -v node)"
  else
    warn 'node' "$node_version -- package.json engines pin 24.x; the build may still work but is untested here"
  fi
else
  fail 'node' 'not installed -- the frontend cannot be built or served'
fi

if have npm; then
  npm_version=$(npm --version 2>/dev/null)
  npm_major=${npm_version%%.*}
  if [ "${npm_major:-0}" -eq 11 ] 2>/dev/null; then
    ok 'npm' "$npm_version"
  else
    warn 'npm' "$npm_version -- package.json engines pin 11.x"
  fi
  npm_prefix=$(npm config get prefix 2>/dev/null)
  [ -n "$npm_prefix" ] && note 'npm prefix' "$npm_prefix"
  npm_cache=$(npm config get cache 2>/dev/null)
  if [ -n "$npm_cache" ]; then
    if [ -d "$npm_cache" ] && ! writable_dir "$npm_cache"; then
      fail 'npm cache' "$npm_cache is not writable -- set npm_config_cache to somewhere you own"
    else
      note 'npm cache' "$npm_cache"
    fi
  fi
else
  fail 'npm' 'not installed'
fi

if [ -d "$REPO_ROOT/frontend/node_modules" ]; then
  count=$(ls "$REPO_ROOT/frontend/node_modules" 2>/dev/null | wc -l | tr -d ' ')
  ok 'frontend/node_modules' "present ($count entries)"
else
  warn 'frontend/node_modules' 'absent -- run: cd frontend && npm ci'
fi

for browser_root in "${PLAYWRIGHT_BROWSERS_PATH:-${HOME:-/nonexistent}/.cache/ms-playwright}"; do
  if [ -d "$browser_root" ]; then
    ok 'playwright browsers' "$browser_root"
  else
    warn 'playwright browsers' "none at $browser_root -- npx playwright install chromium (e2e only; --with-deps needs root)"
  fi
done

# ---------------------------------------------------------------------------
section 'REPOSITORY STATE'
# ---------------------------------------------------------------------------

if have git && [ -d "$REPO_ROOT/.git" ]; then
  note 'git commit' "$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null) on $(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  dirty=$(git -C "$REPO_ROOT" status --porcelain=v1 2>/dev/null | wc -l | tr -d ' ')
  if [ "${dirty:-0}" -eq 0 ]; then
    ok 'git working tree' 'clean'
  else
    note 'git working tree' "$dirty modified or untracked path(s) -- checkpointing in lib/common.sh treats any change as a cache miss"
  fi
else
  warn 'git' 'no repository here, or git is missing -- the phase receipts record a commit and will fail'
fi

# CRLF is the single most common reason "the linux scripts do not work" after a
# transfer from Windows: the interpreter is read as "/usr/bin/env bash\r" and
# the error names a file that plainly exists.
crlf_hits=''
for candidate in "$SCRIPT_DIR"/*.sh "$SCRIPT_DIR"/lib/*.sh "$REPO_ROOT"/scripts/*.sh; do
  [ -f "$candidate" ] || continue
  if head -c 2048 "$candidate" 2>/dev/null | grep -q "$(printf '\r')"; then
    crlf_hits="$crlf_hits $(basename "$candidate")"
  fi
done
if [ -n "$crlf_hits" ]; then
  fail 'line endings' "CRLF found in:$crlf_hits -- fix with: sed -i 's/\\r\$//' scripts/linux/*.sh scripts/*.sh"
else
  ok 'line endings' 'shell scripts are LF'
fi

nonexec=''
for candidate in "$SCRIPT_DIR"/*.sh; do
  [ -f "$candidate" ] || continue
  [ -x "$candidate" ] || nonexec="$nonexec $(basename "$candidate")"
done
if [ -n "$nonexec" ]; then
  warn 'executable bits' "not executable:$nonexec -- run: chmod +x scripts/linux/*.sh (or invoke them as: bash script.sh)"
else
  ok 'executable bits' 'scripts/linux/*.sh are executable'
fi

# ---------------------------------------------------------------------------
section 'CONFIGURATION (.env)'
# ---------------------------------------------------------------------------
#
# Names and presence only. No value from this file is ever printed.

ENV_FILE="$REPO_ROOT/.env"
if [ -f "$ENV_FILE" ]; then
  ok '.env' "present ($(wc -l <"$ENV_FILE" | tr -d ' ') lines)"
  perms=$(ls -l "$ENV_FILE" 2>/dev/null | awk '{print $1}')
  case "$perms" in
    -rw-------*) ok '.env permissions' "$perms" ;;
    *) warn '.env permissions' "$perms -- it holds passwords and API keys; chmod 600 .env" ;;
  esac
  for key in PLATFORM_ENVIRONMENT PLATFORM_MONGO_DSN PLATFORM_SOURCE_MONGO_DSN PLATFORM_NEO4J_URI PLATFORM_SQLSERVER_HOST PLATFORM_VALKEY_HOST PLATFORM_TEMPORAL_TARGET PLATFORM_VAULT_ENABLED; do
    line=$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | first_line)
    if [ -z "$line" ]; then
      warn "env key: $key" 'absent from .env'
    else
      value=${line#*=}
      if [ -z "$value" ]; then
        warn "env key: $key" 'present but empty'
      else
        case "$key" in
          PLATFORM_ENVIRONMENT|PLATFORM_VAULT_ENABLED|PLATFORM_SQLSERVER_HOST|PLATFORM_VALKEY_HOST|PLATFORM_TEMPORAL_TARGET)
            note "env key: $key" "$value" ;;
          *)
            note "env key: $key" "set ($(printf '%s' "$value" | wc -c | tr -d ' ') chars, value withheld)" ;;
        esac
      fi
    fi
  done
  if grep -qE '^PLATFORM_VAULT_ENABLED=(true|1)' "$ENV_FILE" 2>/dev/null; then
    warn 'vault' 'enabled -- the platform then needs a reachable Vault; set PLATFORM_VAULT_ENABLED=false for a local run'
  fi
else
  fail '.env' 'missing -- create it from .env.example, then run scripts/bootstrap_host.sh to generate local credentials'
fi

# ---------------------------------------------------------------------------
section 'CONTAINER RUNTIME'
# ---------------------------------------------------------------------------

if have docker; then
  note 'docker client' "$(docker --version 2>/dev/null | first_line)"
  docker_info=$(docker info --format '{{.ServerVersion}}' 2>&1)
  if [ -n "$docker_info" ] && printf '%s' "$docker_info" | grep -qvE 'permission denied|Cannot connect|error during connect'; then
    ok 'docker daemon' "reachable (server $docker_info)"
    if docker compose version >/dev/null 2>&1; then
      ok 'docker compose' "$(docker compose version 2>/dev/null | first_line)"
    elif have docker-compose; then
      warn 'docker compose' 'only the standalone v1 docker-compose is present; compose.yaml expects the v2 plugin'
    else
      fail 'docker compose' 'missing -- compose.yaml brings up every dependency this platform reads'
    fi
  else
    reason=$(printf '%s' "$docker_info" | tr '\n' ' ' | cut -c1-120)
    if id -Gn 2>/dev/null | grep -qw docker; then
      fail 'docker daemon' "unreachable although you are in the docker group: $reason"
    else
      fail 'docker daemon' "unreachable ($reason) -- you are not in the docker group; either be added to it, or point PLATFORM_* at databases that already exist"
    fi
  fi
elif have podman; then
  warn 'docker' "absent, but podman is present ($(podman --version 2>/dev/null)) -- compose.yaml is untested under podman"
else
  fail 'docker' 'not installed -- either install it, or point the PLATFORM_* DSNs at externally hosted databases'
fi

# ---------------------------------------------------------------------------
section 'PORTS AND DEPENDENCY REACHABILITY'
# ---------------------------------------------------------------------------
#
# Every port compose.yaml publishes, plus the two the host launchers use.
# "listening" is good for a dependency and bad for a port we are about to bind.

for entry in \
  '8000:backend API:bind' \
  '5173:frontend dev server:bind' \
  '27017:mongodb:dependency' \
  '14330:sqlserver (published as 14330 -> 1433):dependency' \
  '7687:neo4j bolt:dependency' \
  '7474:neo4j http:dependency' \
  '6379:valkey:dependency' \
  '7233:temporal:dependency' \
  '8080:temporal ui:dependency'; do
  port=$(printf '%s' "$entry" | cut -d: -f1)
  label=$(printf '%s' "$entry" | cut -d: -f2)
  role=$(printf '%s' "$entry" | cut -d: -f3)
  state=$(port_state "$port")
  case "$role:$state" in
    bind:listening)
      warn "port $port ($label)" 'already in use -- free it with scripts/linux/stop_application_ports.sh before starting' ;;
    bind:free)
      ok "port $port ($label)" 'free' ;;
    dependency:listening)
      ok "port $port ($label)" 'listening' ;;
    dependency:free)
      note "port $port ($label)" 'nothing listening (expected until the stack is up)' ;;
    *)
      note "port $port ($label)" 'state unknown (no ss, netstat or /proc/net/tcp)' ;;
  esac
done

# Ports below 1024 need a capability this host may not grant. Nothing here binds
# one, but a misread of "connection refused" costs an hour, so say it plainly.
note 'privileged ports' 'not required -- every service in compose.yaml publishes above 1024'

# ---------------------------------------------------------------------------
section 'NETWORK'
# ---------------------------------------------------------------------------

for var in http_proxy https_proxy no_proxy HTTP_PROXY HTTPS_PROXY NO_PROXY; do
  value=$(eval "printf '%s' \"\${$var:-}\"")
  if [ -n "$value" ]; then note "proxy: $var" "$value"; fi
done

if [ "$QUICK" -eq 1 ]; then
  note 'network probes' 'skipped (--quick)'
else
  if have getent && getent hosts registry.npmjs.org >/dev/null 2>&1; then
    ok 'dns' 'resolves registry.npmjs.org'
  elif have nslookup && nslookup registry.npmjs.org >/dev/null 2>&1; then
    ok 'dns' 'resolves registry.npmjs.org'
  else
    warn 'dns' 'could not resolve registry.npmjs.org -- offline, or DNS is restricted'
  fi

  if have curl; then
    for endpoint in 'https://pypi.org/simple/:pypi' 'https://registry.npmjs.org/:npm registry'; do
      url=${endpoint%:*}
      name=${endpoint##*:}
      code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 6 "$url" 2>/dev/null)
      case "$code" in
        2*|3*) ok "reachable: $name" "HTTP $code" ;;
        000|'') warn "reachable: $name" 'no response -- installs will fail unless an internal mirror is configured' ;;
        *) warn "reachable: $name" "HTTP $code" ;;
      esac
    done
  else
    note 'network probes' 'curl is absent; skipped'
  fi
fi

# ---------------------------------------------------------------------------
section 'VERDICT'
# ---------------------------------------------------------------------------

printf '  checks: %s ok, %s warning(s), %s blocker(s), %s informational\n' \
  "$OK_COUNT" "$WARN_COUNT" "$FAIL_COUNT" "$INFO_COUNT"

if [ "$FAIL_COUNT" -gt 0 ]; then
  printf '\n  BLOCKERS -- end-to-end will not run until these are resolved:\n\n%s' "$BLOCKERS"
else
  printf '\n  No blockers found.\n'
fi

if [ "$WARN_COUNT" -gt 0 ]; then
  printf '\n  WARNINGS -- likely to bite during a full run:\n\n%s' "$WARNINGS"
fi

cat <<'GUIDE'

  ORDER OF WORK ON A RESTRICTED HOST
  ----------------------------------
  Each step depends only on the ones above it.

    1. Shell and transfer
         bash 4+ present, scripts LF-terminated and executable.
         Fix CRLF:  sed -i 's/\r$//' scripts/linux/*.sh scripts/*.sh scripts/linux/lib/*.sh
         Fix bits:  chmod +x scripts/linux/*.sh

    2. Python
         A 3.13 interpreter, and a working `python3 -m venv`.
         No venv module and no root? Ask for python3-venv, or set
           poetry config --local virtualenvs.create false
         and install into user site instead. The repo's fallbacks accept that:
         `backend_python()` in lib/common.sh tries `poetry run python` before
         it looks at backend/.venv, and run_backend_host.sh execs
         `poetry run uvicorn` whenever poetry is on PATH.

    3. Backend environment
         cd backend
         poetry config --local virtualenvs.in-project true
         poetry env use "$(command -v python3.13)"
         poetry sync

         If Poetry answers "Using virtualenv: /somewhere/else", that setting
         arrived too late: it governs creation, and an environment already
         existed. That is not a problem to fix -- every launcher here runs
         through `poetry run`, which finds it. Only move it if you need
         backend/.venv to exist by name:
           poetry env remove --all && poetry install
         which reinstalls from scratch, so do not start it on a host with no
         package index reachable.

    4. Frontend
         cd frontend && npm ci
         If ~/.npm is not writable:  npm config set cache "$PWD/.npm-cache"

    5. Configuration
         .env present, PLATFORM_VAULT_ENABLED=false for a local run.
         scripts/bootstrap_host.sh generates the local credentials.

    6. Dependencies
         With docker:     docker compose up -d
         Without docker:  point PLATFORM_MONGO_DSN, PLATFORM_SOURCE_MONGO_DSN,
                          PLATFORM_SQLSERVER_*, PLATFORM_NEO4J_*, PLATFORM_VALKEY_*
                          and PLATFORM_TEMPORAL_TARGET at databases that already
                          exist. Nothing in the backend requires a local daemon.

    7. Run
         scripts/run_backend_host.sh      (port 8000)
         scripts/run_frontend_host.sh     (port 5173)
         scripts/run_worker_host.sh       (workers)

  Re-run this report after each step. It is read-only and safe to repeat.

GUIDE

# ---------------------------------------------------------------------------
# Optional machine-readable copy, written only where permission already exists.
# ---------------------------------------------------------------------------

if [ "$WANT_JSON" -eq 1 ]; then
  json_out=''
  for candidate in "$REPO_ROOT/.runtime/linux-validation/evidence" "${TMPDIR:-/tmp}"; do
    if [ -d "$candidate" ] && writable_dir "$candidate"; then
      json_out="$candidate/environment-report.json"
      break
    fi
  done
  json_body="{
  \"schemaVersion\": 1,
  \"generatedAt\": \"$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)\",
  \"repository\": \"$(json_escape "$REPO_ROOT")\",
  \"summary\": {\"ok\": $OK_COUNT, \"warn\": $WARN_COUNT, \"fail\": $FAIL_COUNT, \"info\": $INFO_COUNT},
  \"checks\": [
$(printf '%s' "$JSON_ROWS" | sed '$ s/,$//')
  ]
}"
  if [ -n "$json_out" ] && (printf '%s\n' "$json_body" >"$json_out") 2>/dev/null; then
    printf '  JSON report written to %s\n\n' "$json_out"
  else
    printf '%s\n' "$json_body"
  fi
fi

if [ "$STRICT" -eq 1 ] && [ "$FAIL_COUNT" -gt 0 ]; then
  exit 1
fi
exit 0

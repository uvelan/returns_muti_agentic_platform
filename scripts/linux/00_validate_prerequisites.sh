#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

# `poetry` is deliberately not in this list. `scripts/bootstrap_host.sh` installs
# it into `.tmp/poetry` rather than onto PATH, and phase 02 is what provisions
# the environment -- so demanding it in phase 00 failed every genuinely clean
# machine at the first step, for a tool the pipeline was about to install. The
# phases that need it (03, 05, 07, 15) already fall back to
# `$RUNTIME_ROOT/tooling/bin/poetry` and report a precise error if neither
# exists.
for command in bash git python3 python3.13 node npm docker curl jq tar sha256sum; do
  require_command "$command"
done

# `flock` is a hard requirement of `prepare_runtime_configuration.sh`, which
# every host launcher runs before the backend starts. Failing here names the
# missing package; failing there names a lock file.
require_command flock

# `stop_application_ports.sh` needs one of these to find, and one of the last
# two to stop, whatever owns 8000 and 5173. `run_all_host.sh` calls it before
# it starts anything, so a host with none of them cannot start the application.
if ! command -v ss >/dev/null 2>&1 \
  && ! command -v fuser >/dev/null 2>&1 \
  && ! command -v lsof >/dev/null 2>&1; then
  echo "One of ss, fuser or lsof is required to manage application ports." >&2
  exit 2
fi
if ! command -v fuser >/dev/null 2>&1 && ! command -v lsof >/dev/null 2>&1; then
  echo "Either fuser (psmisc) or lsof is required to stop application port owners." >&2
  exit 2
fi

python_version="$(python3.13 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
[[ "$python_version" == "3.13" ]] || {
  echo "Python 3.13 is required; found $python_version." >&2
  exit 2
}

node_major="$(node -p 'process.versions.node.split(".")[0]')"
[[ "$node_major" == "24" ]] || {
  echo "Node.js 24 is required; found $(node --version)." >&2
  exit 2
}

npm_major="$(npm --version | cut -d. -f1)"
[[ "$npm_major" == "11" ]] || {
  echo "npm 11 is required; found $(npm --version)." >&2
  exit 2
}

docker compose version >/dev/null
docker info >/dev/null

[[ -f "$REPO_ROOT/.env" ]] || {
  cat >&2 <<'EOF'
Root .env is missing. Create it and generate local credentials with:
  ./scripts/bootstrap_host.sh
EOF
  exit 2
}

python3.13 "$LINUX_SCRIPT_DIR/validate_env.py" "$REPO_ROOT/.env" --simulation

# Advisory, not a gate: phase 06 starts Vault, and on a first run there is
# nothing to report yet. But on a *re-run* against an already-running stack this
# single line is the difference between "the platform is broken" and "the Vault
# container restarted". Never fails the phase.
printf 'vault: %s\n' "$(vault_seal_state)"

printf 'Linux prerequisites, Docker daemon, and safe simulation configuration validated.\n'
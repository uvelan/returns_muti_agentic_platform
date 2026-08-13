#!/usr/bin/env bash
#
# SEC-01 -- remove the nine committed credentials from this repository's history.
#
# READ scripts/security/SEC-01_HISTORY_PURGE_RUNBOOK.md BEFORE RUNNING THIS.
#
# This script rewrites every commit in the repository and force-pushes the
# result. Every existing clone, fork, worktree, open pull request and in-flight
# branch becomes invalid the moment it completes. It is therefore:
#
#   * DRY RUN BY DEFAULT -- it prints the plan and changes nothing.
#   * refusing to execute without --execute AND an explicit confirmation phrase.
#   * refusing to execute against anything other than a fresh mirror clone.
#
# It does not run at development time. It runs once, at integration cutover,
# with every other branch merged or abandoned, coordinated by whoever owns the
# repository.
#
# Usage:
#   scripts/security/purge_history_secrets.sh --plan                # default
#   SEC01_PURGE_CONFIRM='PURGE SEC-01 HISTORY' \
#     scripts/security/purge_history_secrets.sh --execute \
#       --mirror /secure/sec01/returns.git \
#       --backup-dir /secure/sec01/backup \
#       --baseline /path/to/checkout/scripts/security/known_exposures.json

set -euo pipefail

# --------------------------------------------------------------------------
# Constants -- the exact purge targets, established by
# scripts/security/scan_secrets.py --mode history at baseline 0615921.
# --------------------------------------------------------------------------

readonly EXPECTED_REMOTE="https://github.com/uvelan/returns_muti_agentic_platform.git"
readonly CONFIRM_PHRASE="PURGE SEC-01 HISTORY"

# Paths deleted from history outright. Neither exists at HEAD; both were
# untracked (5b5392a, 0615921) but remain fully recoverable from history.
readonly -a PURGE_PATHS=(
    ".env.vault-backup"
    "backend/.env.vault-backup"
)

# Live file whose historical content must be redacted rather than deleted:
# backend/tests/conftest.py carried the NVIDIA and Google keys from 52732a5
# until fbfcf05 removed them. --replace-text handles it; the file itself stays.
readonly REDACT_ONLY_PATH="backend/tests/conftest.py"

MODE="plan"
MIRROR=""
BACKUP_DIR=""
BASELINE=""
REMOTE_NAME="origin"

# --------------------------------------------------------------------------

die()  { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
info() { printf '  %s\n' "$*"; }
step() { printf '\n== %s ==\n' "$*"; }

usage() {
    sed -n '3,30p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --plan)        MODE="plan" ;;
        --execute)     MODE="execute" ;;
        --mirror)      MIRROR="${2:-}"; shift ;;
        --backup-dir)  BACKUP_DIR="${2:-}"; shift ;;
        --baseline)    BASELINE="${2:-}"; shift ;;
        --remote)      REMOTE_NAME="${2:-}"; shift ;;
        -h|--help)     usage ;;
        *)             die "unknown argument: $1" ;;
    esac
    shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
: "${BASELINE:=$REPO_ROOT/scripts/security/known_exposures.json}"

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPLACEMENTS=""

cleanup() {
    # The replacements file is every leaked credential in plaintext. It must not
    # survive this process under any exit path, including a failed filter-repo.
    if [ -n "$REPLACEMENTS" ] && [ -f "$REPLACEMENTS" ]; then
        if command -v shred >/dev/null 2>&1; then
            shred --remove --zero "$REPLACEMENTS" 2>/dev/null || rm -f "$REPLACEMENTS"
        else
            rm -f "$REPLACEMENTS"
        fi
        info "removed the temporary replacements file"
    fi
}
trap cleanup EXIT INT TERM

# ==========================================================================
step "SEC-01 history purge -- mode: $MODE"
# ==========================================================================

cat <<'PLAN'
  Removes from history:
    .env.vault-backup            (added bb9bf2e, untracked 0615921)
    backend/.env.vault-backup    (added bb9bf2e, untracked 5b5392a)

  Redacts in place (file survives, values do not):
    backend/tests/conftest.py    (keys present 52732a5..65106ce, removed fbfcf05)

  Credentials removed (9 distinct values, identified by sha256[:16] in
  scripts/security/known_exposures.json -- values appear nowhere in the repo):
    NVIDIA  provider key            x1
    Google  provider key            x2
    SQL Server / Neo4j / MongoDB root / Mongo replica-set key /
    Temporal DB / Valkey passwords  x6
PLAN

# --------------------------------------------------------------------------
step "Preflight"
# --------------------------------------------------------------------------

command -v git >/dev/null 2>&1 || die "git is not on PATH"
info "git: $(git --version)"

if command -v git-filter-repo >/dev/null 2>&1 || git filter-repo --version >/dev/null 2>&1; then
    info "git-filter-repo: available"
    FILTER_REPO_READY=1
else
    FILTER_REPO_READY=0
    cat <<'MISSING'
  git-filter-repo is NOT installed. Install it before executing:

      python -m pip install --user git-filter-repo

  BFG fallback (only if filter-repo cannot be installed). BFG cannot delete a
  path and redact text in one pass, so it needs two, and it never rewrites the
  commit currently checked out -- which is fine here because both target paths
  were already untracked before HEAD:

      java -jar bfg.jar --delete-files '.env.vault-backup' <mirror>
      java -jar bfg.jar --replace-text <replacements-file> <mirror>
      git -C <mirror> reflog expire --expire=now --all
      git -C <mirror> gc --prune=now --aggressive

  BFG's --delete-files matches by FILENAME, not path, so it removes both
  `.env.vault-backup` and `backend/.env.vault-backup` in one rule. Confirm that
  it removed nothing else before pushing.
MISSING
fi

[ -f "$BASELINE" ] || die "baseline not found: $BASELINE"
info "baseline: $BASELINE"

if [ ! -f "$REPO_ROOT/scripts/security/scan_secrets.py" ]; then
    die "scan_secrets.py not found; the purge cannot be verified without it"
fi

if [ "$MODE" = "plan" ]; then
    cat <<PLANNED

== Planned command sequence (NOT executed) ==

  1. Backup, outside the exposed history:
       git clone --mirror $EXPECTED_REMOTE <mirror>
       git -C <mirror> bundle create <backup-dir>/pre-purge-$TIMESTAMP.bundle --all
       sha256sum <backup-dir>/pre-purge-$TIMESTAMP.bundle > <...>.bundle.sha256
       chmod 600 <backup-dir>/pre-purge-$TIMESTAMP.bundle

  2. Recover the values to replace (never committed anywhere):
       python scripts/security/build_replacements.py \\
         --repo <mirror> --baseline $BASELINE --out <secure-tmp>/replacements.txt

  3. Rewrite:
       git -C <mirror> filter-repo \\
         --invert-paths --path .env.vault-backup --path backend/.env.vault-backup \\
         --replace-text <secure-tmp>/replacements.txt

  4. Verify (must report 0 findings):
       python scripts/security/scan_secrets.py --repo <mirror> \\
         --mode history --no-allowlist

  5. Force-push to $EXPECTED_REMOTE:
       git -C <mirror> remote add $REMOTE_NAME $EXPECTED_REMOTE
       git -C <mirror> push --force $REMOTE_NAME --all
       git -C <mirror> push --force $REMOTE_NAME --tags

  6. Invalidate every clone and worktree (see the runbook, section 7).

Re-run with --execute plus SEC01_PURGE_CONFIRM='$CONFIRM_PHRASE' to perform it.
PLANNED
    exit 0
fi

# ==========================================================================
# From here down the run is destructive.
# ==========================================================================

step "Execution guards"

[ "${SEC01_PURGE_CONFIRM:-}" = "$CONFIRM_PHRASE" ] || \
    die "set SEC01_PURGE_CONFIRM='$CONFIRM_PHRASE' to execute"
[ "$FILTER_REPO_READY" = "1" ] || \
    die "git-filter-repo is required for --execute (or run the BFG fallback by hand)"
[ -n "$MIRROR" ]     || die "--mirror <path> is required for --execute"
[ -n "$BACKUP_DIR" ] || die "--backup-dir <path> is required for --execute"

# Four attestations that cannot be checked mechanically and must not be assumed.
cat <<'ATTEST'

  Confirm ALL of the following before continuing. Each is a thing this script
  cannot verify and must not assume:

    1. Every one of the 9 credentials has been ROTATED, and the old values were
       confirmed REJECTED at their provider / service -- not merely replaced.
    2. Every track's work is merged or abandoned. Every open pull request is
       merged or closed. This rewrite invalidates all of them.
    3. Every fork has been deleted or its owner has been told to re-clone.
    4. You are authorised to force-push to the remote below.

ATTEST
printf '  Type the confirmation phrase to proceed: '
read -r typed
[ "$typed" = "$CONFIRM_PHRASE" ] || die "confirmation phrase did not match"

step "Protected backup"

mkdir -p "$BACKUP_DIR"
case "$(cd "$BACKUP_DIR" && pwd)" in
    "$REPO_ROOT"|"$REPO_ROOT"/*)
        die "--backup-dir must be outside the repository ($REPO_ROOT)" ;;
esac

if [ ! -d "$MIRROR" ]; then
    info "cloning a fresh mirror -- filter-repo refuses to touch anything else"
    git clone --mirror "$EXPECTED_REMOTE" "$MIRROR"
else
    info "reusing existing mirror: $MIRROR"
    actual="$(git -C "$MIRROR" remote get-url origin 2>/dev/null || echo '')"
    [ "$actual" = "$EXPECTED_REMOTE" ] || \
        die "mirror points at '$actual', expected '$EXPECTED_REMOTE'"
fi

BUNDLE="$BACKUP_DIR/pre-purge-$TIMESTAMP.bundle"
git -C "$MIRROR" bundle create "$BUNDLE" --all
sha256sum "$BUNDLE" > "$BUNDLE.sha256" 2>/dev/null || \
    shasum -a 256 "$BUNDLE" > "$BUNDLE.sha256"
chmod 600 "$BUNDLE" "$BUNDLE.sha256" 2>/dev/null || true
info "backup bundle: $BUNDLE"
info "THE BUNDLE STILL CONTAINS THE CREDENTIALS. Treat it as a secret:"
info "  offline or access-controlled storage only, never a repository, never a"
info "  shared drive, and destroy it once the purge is accepted."

# A tag pinning the pre-purge tip, kept LOCAL to the mirror. It is deliberately
# not pushed: pushing it back to the remote would re-publish the very history
# this purge removes.
PRE_PURGE_TIP="$(git -C "$MIRROR" rev-parse HEAD)"
git -C "$MIRROR" tag -f "sec01/pre-purge-$TIMESTAMP" "$PRE_PURGE_TIP" >/dev/null
info "pre-purge tip: $PRE_PURGE_TIP (local tag sec01/pre-purge-$TIMESTAMP)"

step "Recovering replacement values"

SECURE_TMP="$(mktemp -d)"
chmod 700 "$SECURE_TMP" 2>/dev/null || true
REPLACEMENTS="$SECURE_TMP/replacements.txt"

python "$REPO_ROOT/scripts/security/build_replacements.py" \
    --repo "$MIRROR" --baseline "$BASELINE" --out "$REPLACEMENTS" \
    || die "could not build the replacement set; history was NOT rewritten"

step "Rewriting history"

FILTER_ARGS=()
for path in "${PURGE_PATHS[@]}"; do
    FILTER_ARGS+=(--path "$path")
done

info "removing: ${PURGE_PATHS[*]}"
info "redacting in place: $REDACT_ONLY_PATH (and every other blob carrying a value)"

git -C "$MIRROR" filter-repo \
    --invert-paths "${FILTER_ARGS[@]}" \
    --replace-text "$REPLACEMENTS"

cleanup
REPLACEMENTS=""

step "Verification"

# --no-allowlist: the baseline must NOT be able to make this pass. After a
# successful purge the count is zero on its own merits.
if ! python "$REPO_ROOT/scripts/security/scan_secrets.py" \
        --repo "$MIRROR" --mode history --no-allowlist; then
    die "history still contains credential-shaped values. DO NOT PUSH. \
Restore from $BUNDLE and investigate."
fi
info "history scan clean"

for path in "${PURGE_PATHS[@]}"; do
    if git -C "$MIRROR" log --all --oneline -- "$path" | grep -q .; then
        die "$path still appears in history. DO NOT PUSH."
    fi
    info "$path: gone from every ref"
done

step "Push"

if ! git -C "$MIRROR" remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
    # filter-repo drops the remote on purpose, so an accidental push cannot
    # happen before the rewrite has been reviewed.
    git -C "$MIRROR" remote add "$REMOTE_NAME" "$EXPECTED_REMOTE"
fi

info "about to force-push the rewritten history to $EXPECTED_REMOTE"
printf '  Type the confirmation phrase once more to push: '
read -r typed_push
[ "$typed_push" = "$CONFIRM_PHRASE" ] || \
    die "not pushed. The rewritten mirror is at $MIRROR if you want to inspect it."

# --force, not --force-with-lease: a lease compares against a ref this clone has
# already rewritten, so it can never match. The guard is the human confirmation
# above and the fact that every branch was merged or abandoned first.
git -C "$MIRROR" push --force "$REMOTE_NAME" --all
git -C "$MIRROR" push --force "$REMOTE_NAME" --tags

step "Done -- the work is not finished"

cat <<'AFTER'
  Immediately:

    1. Every clone and worktree of this repository is now invalid. They will
       fetch a history that shares no commits with theirs and a pull will
       produce an unrelated-histories merge. Do not merge; re-clone.

         git worktree list                     # in each old checkout
         git worktree remove <path> --force    # for each
         cd .. && rm -rf <old-checkout>
         git clone <remote> <fresh-checkout>

       This repository uses .claude/worktrees/agent-* worktrees. Every one of
       them is pinned to a pre-purge commit and must be recreated.

    2. Ask GitHub Support to purge cached views and stale references. Rewritten
       objects stay reachable through the API and through any fork until they
       do. Deleting forks yourself is faster than waiting.

    3. Every open pull request now targets commits that no longer exist. Close
       and re-open them from re-cloned branches.

    4. Confirm push protection is still ON:
         Settings -> Code security -> Secret protection -> Push protection
       The purge does not change it, but confirm rather than assume.

    5. Empty the `known_exposures` array in
       scripts/security/known_exposures.json and delete the [allowlist] section
       from .gitleaks.toml, then commit. Leaving them in place re-permits the
       purged paths.

    6. Destroy the pre-purge bundle once the purge is accepted. Until then it is
       the only rollback, and it is also a copy of every leaked credential.
AFTER

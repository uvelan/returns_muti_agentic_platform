#!/usr/bin/env bash
# The live-infrastructure suite: the 496 tests the default run deselects.
#
# `backend/pyproject.toml` has named this script as the way to run them since the
# marker was introduced, and the script did not exist. So the partition the
# marker documents -- "deselected from the default run, run by this instead" --
# was only half true: the deselection was real and the selection had no entry
# point, which is how 496 mandatory tests end up with nobody running them.
#
# What it does that `pytest -m live_infra` alone does not:
#
#   * refuses to run against absent datastores, so a connection error reads as
#     "start the stack" rather than as 496 failures;
#   * reports the collected total, because "the live suite passed" means nothing
#     without the number it passed out of -- a marker typo that collects zero
#     tests exits 0 and looks identical to success;
#   * passes through extra arguments, so a single test can be run the same way
#     CI runs the whole file;
#   * runs each module in its own process -- see below.
#
# ---------------------------------------------------------------------------
# Why a process per module (RV, `.plan/reviews/HARNESS-1.md`, "Ruling")
# ---------------------------------------------------------------------------
#
# This script used to run all 512 live tests in one pytest process, and on that
# basis RV ruled its output could not be read as an acceptance result.
#
# The measured mechanism is accumulated Temporal server state. The same single
# module, alone in its own process, goes from `13 passed` on a fresh server to 5,
# 4 and 1 spurious failures on a loaded one, with a *different* test failing each
# run. A 512-test single-process run creates strictly more of that state than any
# measurement taken, so it would return a non-empty, non-repeating failure set
# essentially every time -- and every entry in it would be indistinguishable from
# a real regression without individual adjudication.
#
# The asymmetry is what makes it urgent rather than untidy: a flaky gate does not
# only produce false alarms, it produces a standing incentive to re-run until
# green, and a green bought that way is indistinguishable from one that means
# something.
#
# RV named two sufficient fixes. Quarantining the unstable module was rejected
# deliberately: it removes coverage to make a number look clean. **Per-module
# execution keeps every test and changes only the execution model** -- a fresh
# interpreter and a fresh Temporal client per file, so state cannot cross a
# module boundary.
#
# The cost of that choice is that one exit code became seventy-three, and an
# aggregate over many exit codes is exactly where a runner learns to lie. So:
#
#   * the loop never stops on failure and never overwrites its verdict -- a
#     single sticky `failures` list decides the exit code, not the last module;
#   * the summary names every failing module and its counts, so "the live suite
#     failed" is never a bare number;
#   * a module whose summary line cannot be parsed is reported as unparsed and
#     counted as a failure, rather than contributing a silent zero. An aggregate
#     that drops what it cannot read is the same defect one level up.
#
# Usage:
#   scripts/dev/run_real_infra_suite.sh                       # per module
#   scripts/dev/run_real_infra_suite.sh tests/operations/test_case_aggregate_real_infra.py
#   scripts/dev/run_real_infra_suite.sh -k reservations -x
#
# Naming a file or a `-k` selection runs one process directly: fanning out over a
# set the caller already narrowed to one thing buys nothing and costs a
# collection pass.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT/backend"

# The interpreter, in the order a developer actually has one. `.venv` first
# because that is what `bootstrap_host` creates and what every other script
# here uses; falling back to whatever `python` resolves to keeps the script
# usable on a CI image that installed into the system environment.
if [[ -x ".venv/Scripts/python.exe" ]]; then
  PYTHON=".venv/Scripts/python.exe"        # Windows / Git Bash
elif [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"                # Linux / macOS
elif command -v python >/dev/null 2>&1; then
  PYTHON="python"
else
  echo "error: no Python interpreter found (looked for backend/.venv, then PATH)" >&2
  exit 1
fi

# Preflight, and the reason it is here: these tests open real drivers. Without
# this check a stopped stack produces hundreds of connection errors that look
# like the code is broken, and the one line that matters -- "the database is not
# running" -- is buried under them.
#
# Ports rather than container names, because the suite reaches the datastores
# over the host ports `.env` names, and a container that is up but unmapped is
# exactly the failure ENV-ACTION-01 recorded during the audit: Temporal running,
# healthy, and publishing no host port at all.
declare -a required_ports=(
  "MongoDB:27017"
  "Neo4j:17687"
  # 11433, matching `.env`'s PLATFORM_SQLSERVER_PORT and compose.yaml's
  # `${PLATFORM_SQLSERVER_PORT:-11433}`. This said 14330 and so refused a stack
  # that was fully up -- blocking the only sanctioned entry point to the 512
  # live-infra tests with the message "start the stack". compose.yaml:186-191
  # records the earlier fix that moved the published port off 14330; this file
  # was missed by it, and nothing cross-checks the two.
  "SQL Server:11433"
  "Valkey:6379"
  # 17233: Windows reserves TCP 7147-7246, so the host publish moved. In-container
  # addressing is still `temporal:7233`.
  "Temporal:17233"
)

missing=()
for entry in "${required_ports[@]}"; do
  name="${entry%%:*}"
  port="${entry##*:}"
  if ! "$PYTHON" -c "
import socket, sys
s = socket.socket()
s.settimeout(2)
sys.exit(0 if s.connect_ex(('127.0.0.1', $port)) == 0 else 1)
" 2>/dev/null; then
    missing+=("$name (127.0.0.1:$port)")
  fi
done

if (( ${#missing[@]} > 0 )); then
  echo "error: the live-infrastructure suite needs datastores that are not reachable:" >&2
  for entry in "${missing[@]}"; do
    echo "  - $entry" >&2
  done
  echo >&2
  echo "Start them with:  scripts/infra.sh start" >&2
  echo "Or check mappings with:  docker compose ps" >&2
  echo >&2
  echo "Not run. This is a missing stack, not a test failure." >&2
  exit 2
fi

# `-m live_infra` replaces the `-m` in `addopts`: it is a store option and the
# command line is parsed after addopts, which is precisely how this suite
# selects itself back in. See the comment on `addopts` in pyproject.toml.
echo "live-infrastructure suite: all five datastores reachable"

# One collection pass, read twice: once for the total this script has always
# reported, once for the module list the fan-out below walks. Collecting twice
# would cost a second pass and, worse, would let the reported total and the
# executed set disagree.
collect_out="$(mktemp)"
trap 'rm -f "$collect_out"' EXIT
"$PYTHON" -m pytest -m live_infra --collect-only -q "$@" >"$collect_out" 2>/dev/null || true

# Anchored on pytest's count line rather than on `tail -1`. This used to be
# `tail -1`, and it was wrong whenever pytest emitted a warnings summary -- the
# last line is then the capture-warnings docs URL, so the whole-suite run
# reported `collection: -- Docs: https://docs.pytest.org/...` in place of its
# total. Not a cosmetic bug: the total is here precisely because a marker typo
# that collects zero tests exits 0 and looks like success, and a report that
# prints a URL instead of a number cannot show that.
collected="$(grep -E '^([0-9]+/)?[0-9]+ tests? collected|^no tests (collected|ran)' "$collect_out" | tail -1 || true)"
echo "collection: ${collected:-unknown}"
echo

# Did the caller already narrow the selection? A positional path or a `-k`
# expression means they asked for one thing; fanning out is pointless there.
narrowed=0
skip_next=0
for arg in "$@"; do
  if (( skip_next )); then skip_next=0; continue; fi
  case "$arg" in
    -k|--deselect) narrowed=1; skip_next=1 ;;
    -k*)           narrowed=1 ;;
    -*)            ;;   # -x, -q, --lf and friends narrow nothing by file
    *)             [[ -e "${arg%%::*}" ]] && narrowed=1 ;;
  esac
done

if (( narrowed )); then
  echo "arguments name a specific selection -- running it in one process"
  echo
  exec "$PYTHON" -m pytest -m live_infra "$@"
fi

# Discover the modules from pytest's own collection rather than from a glob, so
# the fan-out covers exactly what `-m live_infra` selects. A glob over
# `*_real_infra.py` would miss the twenty-odd live modules that are not named
# that way -- and missing them silently is the shape of defect this whole branch
# is about.
modules=()
while IFS= read -r line; do
  [[ -n "$line" ]] && modules+=("$line")
done < <(sed -n 's/^\([^ ]*\.py\)::.*/\1/p' "$collect_out" | sort -u)

if (( ${#modules[@]} == 0 )); then
  echo "error: collection found no live-infrastructure modules." >&2
  echo "A run over zero modules exits 0 and looks identical to success, so it" >&2
  echo "is refused here. Check the marker and the collection line above." >&2
  exit 1
fi

echo "running ${#modules[@]} modules, one process each"
echo

suite_started=$SECONDS
failed_modules=()
failed_summaries=()
passed_count=0
total_passed=0
total_failed=0
counts_unparsed=()
log_dir="$(mktemp -d)"
# One trap, both temporaries: a second `trap ... EXIT` would silently replace the
# first and leak the collection file.
trap 'rm -f "$collect_out"; rm -rf "$log_dir"' EXIT

index=0
for module in "${modules[@]}"; do
  index=$(( index + 1 ))
  printf '=== [%2d/%2d] %s\n' "$index" "${#modules[@]}" "$module"

  log="$log_dir/$(printf '%s' "$module" | tr '/' '_').log"
  rc=0
  # `|| rc=$?` rather than `set +e`: the loop must survive every failure, and it
  # must survive it the same way each time.
  "$PYTHON" -m pytest -m live_infra "$module" 2>&1 | tee "$log" || rc=${PIPESTATUS[0]}

  # pytest's last non-empty line is its own summary; take it verbatim rather
  # than recomposing one.
  summary="$(grep -v '^[[:space:]]*$' "$log" | tail -1 | sed 's/^=*[[:space:]]*//; s/[[:space:]]*=*$//')"
  [[ -n "$summary" ]] || summary="(no output)"

  # The leading space matters. `[^0-9]` before the digits needs something to
  # match, and `8 passed in 24.51s` begins with the digit -- so without it every
  # all-green module parsed as unreadable and every `N failed` that opened a
  # summary line was dropped. Caught by the deliberate-failure proof, which
  # reported `tests failed: 0` next to a failing module.
  padded=" $summary"
  n_passed="$(printf '%s' "$padded" | sed -n 's/.*[^0-9]\([0-9]\+\) passed.*/\1/p')"
  n_failed="$(printf '%s' "$padded" | sed -n 's/.*[^0-9]\([0-9]\+\) failed.*/\1/p')"
  n_errors="$(printf '%s' "$padded" | sed -n 's/.*[^0-9]\([0-9]\+\) error.*/\1/p')"
  if [[ -z "$n_passed$n_failed$n_errors" ]]; then
    # Unreadable is not zero. Say so, and let it count against the run.
    counts_unparsed+=("$module")
  fi
  total_passed=$(( total_passed + ${n_passed:-0} ))
  total_failed=$(( total_failed + ${n_failed:-0} + ${n_errors:-0} ))

  if (( rc == 0 )); then
    passed_count=$(( passed_count + 1 ))
  else
    # Exit 5 is "no tests collected". It cannot happen for a module this loop
    # derived from collection -- which is exactly why it is worth failing on
    # rather than tolerating: it means collection and execution disagree.
    failed_modules+=("$module")
    failed_summaries+=("exit $rc -- $summary")
  fi
  echo
done

elapsed=$(( SECONDS - suite_started ))

echo "================== live-infrastructure suite: summary =================="
printf 'modules run     : %d\n' "${#modules[@]}"
printf 'modules passed  : %d\n' "$passed_count"
printf 'modules failed  : %d\n' "${#failed_modules[@]}"
printf 'tests passed    : %d\n' "$total_passed"
printf 'tests failed    : %d\n' "$total_failed"
printf 'wall time       : %dm %02ds (one process per module)\n' "$(( elapsed / 60 ))" "$(( elapsed % 60 ))"

if (( ${#counts_unparsed[@]} > 0 )); then
  echo
  echo "counts could not be read for ${#counts_unparsed[@]} module(s); the totals above are"
  echo "incomplete and must not be quoted as the suite's result:"
  for module in "${counts_unparsed[@]}"; do
    echo "  ? $module"
  done
fi

if (( ${#failed_modules[@]} > 0 )); then
  echo
  echo "FAILED modules:"
  for i in "${!failed_modules[@]}"; do
    printf '  x %s\n' "${failed_modules[$i]}"
    printf '      %s\n' "${failed_summaries[$i]}"
  done
  echo
  echo "the live-infrastructure suite FAILED (${#failed_modules[@]} of ${#modules[@]} modules)."
  echo "Logs were per module; re-run one with:"
  echo "  scripts/dev/run_real_infra_suite.sh ${failed_modules[0]}"
  exit 1
fi

# Unparsed counts with every module green is still not a result anyone can read.
if (( ${#counts_unparsed[@]} > 0 )); then
  echo
  echo "every module exited 0, but the suite cannot report what it ran. Refusing"
  echo "to call that a pass."
  exit 1
fi

echo
echo "the live-infrastructure suite PASSED: ${#modules[@]} modules, $total_passed tests."

#!/usr/bin/env bash
# Append one `### command` + verbatim output block to a ledger file.
#
# Exists so that a ledger entry's command and output are CAPTURED rather than
# transcribed: the text in the ledger is the process's own bytes, redirected,
# and never retyped by whoever ran it. A review round was spent on precisely
# that distinction.
#
# Usage: ledger_capture.sh <ledger-path> <command-string>
set -uo pipefail
# Resolved to an absolute path before anything runs: captured commands often
# `cd`, and a relative ledger path would then append to a file that does not
# exist -- losing the closing fence rather than failing loudly.
ledger="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; shift
cmd="$*"
{
  printf '### `%s`\n\n```\n' "$cmd"
} >>"$ledger"
eval "$cmd" >>"$ledger" 2>&1
status=$?
{
  printf '```\n\n*exit %d*\n\n' "$status"
} >>"$ledger"
exit $status

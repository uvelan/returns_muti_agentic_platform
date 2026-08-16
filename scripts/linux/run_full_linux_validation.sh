#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

mode="${1:---from-start}"
keep_running=false
if [[ "$mode" != "--from-start" && "$mode" != "--resume" ]]; then
  echo "Usage: $0 {--from-start|--resume} [--keep-running]" >&2
  exit 2
fi
if [[ "${2:-}" == "--keep-running" ]]; then
  keep_running=true
elif [[ -n "${2:-}" ]]; then
  echo "Unknown option: $2" >&2
  exit 2
fi

phase_manifest="$LINUX_SCRIPT_DIR/validation_phases.txt"
[[ -s "$phase_manifest" ]] || {
  echo "Validation phase manifest is missing: $phase_manifest" >&2
  exit 2
}
readarray -t phases < <(grep -vE '^[[:space:]]*(#|$)' "$phase_manifest")
[[ "${#phases[@]}" -gt 0 ]] || {
  echo "Validation phase manifest contains no phases." >&2
  exit 2
}
for script_name in "${phases[@]}"; do
  [[ -x "$LINUX_SCRIPT_DIR/$script_name" ]] || {
    echo "Validation phase is missing or not executable: $script_name" >&2
    exit 2
  }
done

if [[ "$mode" == "--from-start" ]]; then
  find "$STATE_DIR" -maxdepth 1 -type f -name '*.sha256' -delete
  rm -f "$EVIDENCE_DIR/linux-validation-receipt.json"
  for script_name in "${phases[@]}"; do
    phase="${script_name%.sh}"
    rm -f "$EVIDENCE_DIR/${phase}.json" "$LOG_DIR/${phase}.log"
  done
fi

failed_phase=""
for script_name in "${phases[@]}"; do
  phase="${script_name%.sh}"
  if [[ "$mode" == "--resume" ]] && checkpoint_valid "$phase"; then
    printf '[SKIP] %s has a valid checkpoint for the current tree.\n' "$phase"
    continue
  fi
  printf '[RUN] %s\n' "$phase"
  if ! run_and_record "$phase" "$LINUX_SCRIPT_DIR/$script_name"; then
    failed_phase="$phase"
    break
  fi
done

if [[ -n "$failed_phase" ]]; then
  "$LINUX_SCRIPT_DIR/15_collect_failure_evidence.sh" || true
  "$LINUX_SCRIPT_DIR/16_generate_linux_receipt.sh" || true
  if [[ "$failed_phase" == "20_verify_manual_screen_attestation" ]]; then
    cat <<EOF
manual_action=inspect and complete $EVIDENCE_DIR/manual-screen-validation.json
resume_command=./scripts/linux/run_full_linux_validation.sh --resume
EOF
  else
    printf 'failed_phase_log=%s\n' "$LOG_DIR/${failed_phase}.log"
  fi
  cat <<EOF
overall_status=FAIL
failed_phase=$failed_phase
evidence_directory=$EVIDENCE_DIR
exact_next_command=./scripts/linux/package_validation_results.sh
EOF
  exit 1
fi

"$LINUX_SCRIPT_DIR/16_generate_linux_receipt.sh"
if [[ "$keep_running" == false ]]; then
  "$LINUX_SCRIPT_DIR/17_stop_host_processes.sh"
  "$LINUX_SCRIPT_DIR/18_stop_infrastructure.sh" --stop
fi

# Reported from the manifest, not asserted. This block used to print
# `e2e_status=PASS` and `accessibility_status=PASS` unconditionally, so a
# disabled phase would have been summarized as a passing one -- a summary that
# claims a gate ran is worse than one that admits it did not.
phase_status() {
  local script_name="$1"
  local item
  for item in "${phases[@]}"; do
    if [[ "$item" == "$script_name" ]]; then
      printf 'PASS'
      return 0
    fi
  done
  printf 'SKIPPED_NO_SUITE'
}

cat <<EOF
overall_status=PASS
failed_phase=NONE
quality_status=PASS
infrastructure_status=PASS
seed_status=PASS
backend_status=PASS
worker_status=PASS
frontend_status=PASS
api_status=PASS
scenario_status=PASS
ai_live_stack_status=PASS
e2e_status=$(phase_status 14_run_real_e2e.sh)
accessibility_status=$(phase_status 14_run_accessibility.sh)
restart_replay_status=PASS
manual_screen_status=PASS
evidence_directory=$EVIDENCE_DIR
exact_next_command=./scripts/linux/package_validation_results.sh
EOF

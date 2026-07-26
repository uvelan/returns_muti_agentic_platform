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

if [[ "$mode" == "--from-start" ]]; then
  find "$STATE_DIR" -maxdepth 1 -type f -name '*.sha256' -delete
fi

declare -a phases=(
  "01_verify_transfer.sh"
  "02_reconstruct_environment.sh"
  "03_run_backend_quality.sh"
  "04_run_frontend_quality.sh"
  "05_run_contract_and_config_checks.sh"
  "06_start_infrastructure.sh"
  "07_seed_and_validate_data.sh"
  "08_start_backend.sh"
  "09_start_workers.sh"
  "10_start_frontend.sh"
  "11_validate_host_processes.sh"
  "12_validate_worker_heartbeats.sh"
  "13_run_api_probes.sh"
  "14_run_real_e2e.sh"
  "19_verify_repository_state.sh"
)

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
e2e_status=PASS
evidence_directory=$EVIDENCE_DIR
exact_next_command=./scripts/linux/package_validation_results.sh
EOF

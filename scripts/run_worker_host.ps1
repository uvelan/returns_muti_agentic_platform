param([Parameter(Mandatory=$true)][ValidateSet("temporal","orchestrator","outbox","jobs","integration-outbox")][string]$Worker)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $Root "scripts\vault\export_runtime_vault_env.ps1")
$Scripts = @{
  temporal = "run_return_workflow_worker.py"
  orchestrator = "run_return_orchestrator.py"
  outbox = "run_outbox_publisher.py"
  jobs = "run_data_job_worker.py"
}
Push-Location (Join-Path $Root "backend")
try {
  $env:PYTHONPATH = (Join-Path $Root "backend\src")
  if ($Worker -eq "integration-outbox") {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
      uv run python -m return_platform.workers.integration_outbox
    } elseif (Get-Command poetry -ErrorAction SilentlyContinue) {
      poetry run python -m return_platform.workers.integration_outbox
    } else {
      & .\.venv\Scripts\python.exe -m return_platform.workers.integration_outbox
    }
  } elseif (Get-Command uv -ErrorAction SilentlyContinue) {
    uv run python (Join-Path "scripts" $Scripts[$Worker])
  } elseif (Get-Command poetry -ErrorAction SilentlyContinue) {
    poetry run python (Join-Path "scripts" $Scripts[$Worker])
  } else {
    & .\.venv\Scripts\python.exe (Join-Path "scripts" $Scripts[$Worker])
  }
} finally { Pop-Location }

param([Parameter(Mandatory=$true)][ValidateSet("temporal","orchestrator","outbox","jobs")][string]$Worker)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Scripts = @{
  temporal = "run_return_workflow_worker.py"
  orchestrator = "run_return_orchestrator.py"
  outbox = "run_outbox_publisher.py"
  jobs = "run_data_job_worker.py"
}
Push-Location (Join-Path $Root "backend")
try {
  $env:PYTHONPATH = (Join-Path $Root "backend\src")
  if (Get-Command poetry -ErrorAction SilentlyContinue) {
    poetry run python (Join-Path "scripts" $Scripts[$Worker])
  } else {
    & .\.venv\Scripts\python.exe (Join-Path "scripts" $Scripts[$Worker])
  }
} finally { Pop-Location }

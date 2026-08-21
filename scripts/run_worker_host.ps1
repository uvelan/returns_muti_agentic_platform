# The worker set is the one `scripts/run_worker_host.sh` validates, and it has
# to stay that way: `discovery` is in `REQUIRED_PROCESS_CLASSES`
# (`configuration/process_adoption.py:66`), so a host that never starts it can
# never report a release as LIVE -- adoption sits at ACTIVATING forever with no
# error to read. It was missing here while Linux had already added it.
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet("temporal", "discovery", "orchestrator", "outbox", "integration-outbox", "housekeeping")]
  [string]$Worker
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Scripts = @{
  temporal     = "run_return_workflow_worker.py"
  discovery    = "run_order_discovery_worker.py"
  housekeeping = "run_housekeeping_worker.py"
  orchestrator = "run_return_orchestrator.py"
  outbox       = "run_outbox_publisher.py"
}
Push-Location (Join-Path $Root "backend")
try {
  $env:PYTHONPATH = (Join-Path $Root "backend\src")
  if ($Worker -eq "integration-outbox") {
    if (Test-Path -LiteralPath ".\.venv\Scripts\python.exe") {
      & .\.venv\Scripts\python.exe -m return_platform.workers.integration_outbox
    } elseif (Get-Command poetry -ErrorAction SilentlyContinue) {
      poetry run python -m return_platform.workers.integration_outbox
    } else {
      throw "No backend Python environment is available."
    }
  } elseif (Test-Path -LiteralPath ".\.venv\Scripts\python.exe") {
    & .\.venv\Scripts\python.exe (Join-Path "scripts" $Scripts[$Worker])
  } elseif (Get-Command poetry -ErrorAction SilentlyContinue) {
    poetry run python (Join-Path "scripts" $Scripts[$Worker])
  } else {
    throw "No backend Python environment is available."
  }
} finally { Pop-Location }

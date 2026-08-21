$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

Write-Host "Preparing runtime configuration..." -ForegroundColor Cyan
Push-Location (Join-Path $Root "backend")
try {
  $env:PYTHONPATH = (Join-Path $Root "backend\src")
  # One list, run through whichever interpreter is available. It used to be
  # written out once per branch, and the branches had already drifted -- this
  # is the sequence `prepare_runtime_configuration.sh` runs on Linux, including
  # the SQL migrations that neither branch had.
  $preparation = @(
    (Join-Path "scripts" "apply_sql_migrations.py"),
    (Join-Path "scripts" "apply_neo4j_migrations.py"),
    (Join-Path "scripts" "bootstrap_graph_configuration.py")
  )
  if (Test-Path -LiteralPath ".\.venv\Scripts\python.exe") {
    $run = { param($script) & .\.venv\Scripts\python.exe $script }
  } elseif (Get-Command poetry -ErrorAction SilentlyContinue) {
    $run = { param($script) poetry run python $script }
  } else {
    throw "No backend Python environment is available."
  }
  foreach ($script in $preparation) {
    & $run $script
    if ($LASTEXITCODE -ne 0) { throw "Runtime preparation failed: $script" }
  }
} finally { Pop-Location }

Write-Host "Starting backend, workers, and frontend..." -ForegroundColor Cyan

# Name every child, because the monitor below tears the whole stack down when
# one of them exits and "Process 24916 exited" does not say which one.
$started = @()

function Start-Child {
  param([string]$Name, [string[]]$Arguments)
  $proc = Start-Process powershell -ArgumentList $Arguments -PassThru -WindowStyle Hidden
  return [pscustomobject]@{ Name = $Name; Id = $proc.Id }
}

$started += Start-Child -Name "backend" -Arguments @(
  "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "scripts\run_backend_host.ps1")
)

# The same set `scripts/linux/09_start_workers.sh` starts, and for the same two
# reasons recorded there: `jobs` is gone (the data-console package it imported
# was deleted, so the process died on import at every start -- and because the
# monitor below treats any exit as fatal, that one dead worker took the entire
# host stack down with it), and `discovery` is present because
# `order-discovery-worker` is in `REQUIRED_PROCESS_CLASSES`
# (`configuration/process_adoption.py:66`); without it a release never reaches
# LIVE and adoption sits at ACTIVATING with nothing to read.
foreach ($worker in @("temporal", "discovery", "orchestrator", "outbox", "integration-outbox")) {
  $started += Start-Child -Name "worker-$worker" -Arguments @(
    "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "scripts\run_worker_host.ps1"),
    "-Worker", $worker
  )
}

$started += Start-Child -Name "frontend" -Arguments @(
  "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "scripts\run_frontend_host.ps1")
)

Write-Host "All services started:" -ForegroundColor Green
foreach ($child in $started) { Write-Host ("  {0,-24} pid {1}" -f $child.Name, $child.Id) }

try {
  while ($true) {
    Start-Sleep -Seconds 1
    foreach ($child in $started) {
      if (-not (Get-Process -Id $child.Id -ErrorAction SilentlyContinue)) {
        Write-Host "$($child.Name) (pid $($child.Id)) exited; stopping the stack." -ForegroundColor Yellow
        return
      }
    }
  }
} finally {
  Write-Host "Stopping all child processes..." -ForegroundColor Yellow
  foreach ($child in $started) {
    Stop-Process -Id $child.Id -Force -ErrorAction SilentlyContinue
  }
}

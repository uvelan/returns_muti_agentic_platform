$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $Root "scripts\vault\export_runtime_vault_env.ps1")

Write-Host "Preparing runtime configuration..." -ForegroundColor Cyan
Push-Location (Join-Path $Root "backend")
try {
  $env:PYTHONPATH = (Join-Path $Root "backend\src")
  # One list, run through whichever interpreter is available. It used to be
  # written out once per branch, and the branches had already drifted -- this
  # is the sequence `prepare_runtime_configuration.sh` runs on Linux, including
  # the SQL migrations that neither branch had.
  $preparation = @(
    (Join-Path $Root "scripts\vault\bootstrap_local_vault.py"),
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
$pids = @()

# Start Backend
$proc = Start-Process powershell -ArgumentList "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "scripts\run_backend_host.ps1") -PassThru -WindowStyle Hidden
$pids += $proc.Id

# Start Workers
foreach ($worker in @("temporal", "orchestrator", "outbox", "jobs", "integration-outbox")) {
  $proc = Start-Process powershell -ArgumentList "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "scripts\run_worker_host.ps1"), "-Worker", $worker -PassThru -WindowStyle Hidden
  $pids += $proc.Id
}

# Start Frontend
$proc = Start-Process powershell -ArgumentList "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "scripts\run_frontend_host.ps1") -PassThru -WindowStyle Hidden
$pids += $proc.Id

Write-Host "All services started. PIDs: $($pids -join ', ')" -ForegroundColor Green

try {
  while ($true) {
    Start-Sleep -Seconds 1
    foreach ($id in $pids) {
      if (-not (Get-Process -Id $id -ErrorAction SilentlyContinue)) {
        Write-Host "Process $id exited." -ForegroundColor Yellow
        return
      }
    }
  }
} finally {
  Write-Host "Stopping all child processes..." -ForegroundColor Yellow
  foreach ($id in $pids) {
    Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
  }
}

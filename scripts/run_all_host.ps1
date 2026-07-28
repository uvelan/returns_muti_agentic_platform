$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $Root "scripts\vault\export_runtime_vault_env.ps1")

Write-Host "Preparing runtime configuration..." -ForegroundColor Cyan
Push-Location (Join-Path $Root "backend")
try {
  $env:PYTHONPATH = (Join-Path $Root "backend\src")
  if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv run python (Join-Path $Root "scripts\vault\bootstrap_local_vault.py")
    uv run python (Join-Path "scripts" "apply_neo4j_migrations.py")
    uv run python (Join-Path "scripts" "bootstrap_graph_configuration.py")
  } elseif (Get-Command poetry -ErrorAction SilentlyContinue) {
    poetry run python (Join-Path $Root "scripts\vault\bootstrap_local_vault.py")
    poetry run python (Join-Path "scripts" "apply_neo4j_migrations.py")
    poetry run python (Join-Path "scripts" "bootstrap_graph_configuration.py")
  } else {
    & .\.venv\Scripts\python.exe (Join-Path $Root "scripts\vault\bootstrap_local_vault.py")
    & .\.venv\Scripts\python.exe (Join-Path "scripts" "apply_neo4j_migrations.py")
    & .\.venv\Scripts\python.exe (Join-Path "scripts" "bootstrap_graph_configuration.py")
  }
} finally { Pop-Location }

Write-Host "Starting backend, workers, and frontend..." -ForegroundColor Cyan
$pids = @()

# Start Backend
$proc = Start-Process powershell -ArgumentList "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "scripts\run_backend_host.ps1") -PassThru -NoNewWindow
$pids += $proc.Id

# Start Workers
foreach ($worker in @("temporal", "orchestrator", "outbox", "jobs", "integration-outbox")) {
  $proc = Start-Process powershell -ArgumentList "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "scripts\run_worker_host.ps1"), "-Worker", $worker -PassThru -NoNewWindow
  $pids += $proc.Id
}

# Start Frontend
$proc = Start-Process powershell -ArgumentList "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "scripts\run_frontend_host.ps1") -PassThru -NoNewWindow
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

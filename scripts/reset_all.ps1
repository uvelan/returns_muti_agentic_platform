<#
.SYNOPSIS
  One command to get from any state to a running platform with fresh data, on Windows.

.DESCRIPTION
  The Windows twin of `scripts/linux/reset_all.sh`. The pieces existed on this
  side too -- `run_all_host.ps1` starts the host and the loaders are plain
  Python -- but the sequence did not, and three of the Linux helpers it chains
  (`reset_docker_environment.sh`, `17_stop_host_processes.sh`,
  `06_start_infrastructure.sh`) have no PowerShell equivalent. So the ordering
  is reproduced here rather than shelled out to.

  Order matters and is not arbitrary:

    1. stop the host, so nothing writes while the stores are being dropped
    2. reset and start infrastructure, so the datastores exist and are healthy
    3. load the reference dataset, which drops every database first
    4. start the host, whose bootstrap recreates the system store it just lost
       and applies the SQL migrations
    5. build the graph, which needs the source collections written in (3)

  Step 5 is the one this exists for. Without it Neo4j stays empty and the
  copilot truthfully reports finding no orders, which reads as a broken agent
  rather than a missing build step.

  **Settings are read from `.env` by each process as it starts.** Every host
  process constructs `Settings()` once at import, so this script is also the
  supported way to pick up edited credentials: there is no runtime refresh that
  re-reads the file. Activating a configuration release rebuilds the AI route
  pool, but from the settings the process started with.

.PARAMETER NoHost
  Leave backend, workers and frontend stopped. Steps 1-3 still run.

.PARAMETER Dataset
  Load from a different dataset directory.

.PARAMETER GraphRecords
  Per-asset cap for the graph build. Default 30000, matching the Linux script.

.PARAMETER KeepVolumes
  Restart the containers without deleting their volumes. Much faster, and it
  keeps whatever is in the datastores -- so it is not a reset. Use it when you
  want a clean process restart against existing data.

.PARAMETER Yes
  Skip the confirmation. This script drops every database; the prompt is there
  on purpose.

.EXAMPLE
  ./scripts/reset_all.ps1
  Full reset: fresh containers, fresh data, host running, graph built.

.EXAMPLE
  ./scripts/reset_all.ps1 -KeepVolumes
  Restart every process against the data already there -- the option to reach
  for after editing `.env`.
#>
param(
  [switch]$NoHost,
  [string]$Dataset,
  [int]$GraphRecords = 30000,
  [switch]$KeepVolumes,
  [switch]$Yes
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Backend = Join-Path $Root "backend"

function Step { param([string]$Text) Write-Host "`n[reset] $Text" -ForegroundColor Cyan }
function Note { param([string]$Text) Write-Host "        $Text" -ForegroundColor DarkGray }
function Die  { param([string]$Text) Write-Host "[reset] ERROR: $Text" -ForegroundColor Red; exit 1 }

# --- Preconditions -----------------------------------------------------------

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Die "Docker CLI is not installed." }
docker info *> $null
if ($LASTEXITCODE -ne 0) { Die "Docker daemon is not running or not reachable." }

$python = Join-Path $Backend ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Die "No virtualenv at $python. Run scripts/bootstrap_host.ps1 first." }

if (-not (Test-Path (Join-Path $Root ".env"))) {
  Die "No .env at the repository root. Every process reads its settings from it."
}

if (-not ($Yes -or $KeepVolumes)) {
  Write-Host "`nThis drops every database in the local stack and reloads the reference dataset." -ForegroundColor Yellow
  Write-Host "Pass -KeepVolumes to restart the processes without touching the data." -ForegroundColor Yellow
  # A non-interactive shell -- CI, a hook, an agent -- cannot answer, and
  # `Read-Host` throws there rather than returning. Treat that as the refusal it
  # is: nobody confirmed, so nothing is dropped, and the message says which
  # switch to pass instead of leaving a PSInvalidOperationException as the
  # explanation.
  $answer = $null
  try {
    $answer = Read-Host "Type 'reset' to continue"
  } catch {
    Write-Host "[reset] No console to confirm on. Nothing was changed." -ForegroundColor Green
    Write-Host "        Re-run with -Yes to reset, or -KeepVolumes to restart without touching data." -ForegroundColor DarkGray
    exit 0
  }
  if ($answer -ne "reset") { Write-Host "[reset] Nothing was changed." -ForegroundColor Green; exit 0 }
}

# --- 1. Stop the host --------------------------------------------------------
# Before the stores are dropped, not after: a worker still holding a Temporal
# poll or a Mongo cursor writes into the database being recreated underneath it.

Step "1/5  Stopping host processes"
$patterns = @(
  "uvicorn", "run_return_workflow_worker", "run_order_discovery_worker",
  "run_return_orchestrator", "run_outbox_publisher", "workers.integration_outbox",
  "run_housekeeping_worker", "vite"
)
$stopped = 0
foreach ($proc in Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'node.exe'") {
  $cmd = $proc.CommandLine
  if (-not $cmd) { continue }
  # Anchored on this repository, so a Python process belonging to another
  # checkout on the same machine is left alone.
  if ($cmd -notlike "*$Root*" -and $cmd -notlike "*returns_muti_agentic_platform*") { continue }
  foreach ($pattern in $patterns) {
    if ($cmd -like "*$pattern*") {
      Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
      $stopped++
      break
    }
  }
}
Note "stopped $stopped process(es)"

# --- 2. Infrastructure -------------------------------------------------------

Push-Location $Root
try {
  if ($KeepVolumes) {
    Step "2/5  Restarting infrastructure, keeping volumes"
    docker compose up -d
  } else {
    Step "2/5  Recreating infrastructure (volumes deleted)"
    docker compose down --volumes --remove-orphans
    docker compose up -d
  }
  if ($LASTEXITCODE -ne 0) { Die "docker compose failed." }

  # Healthy, not merely started. The audit's ENV-ACTION-01 was a Temporal
  # container reporting healthy with no published port, because its healthcheck
  # runs inside the container; waiting on `docker compose ps` status is the
  # cheap half of that lesson, and the port probe below is the other half.
  Step "      Waiting for containers to report healthy"
  $deadline = (Get-Date).AddMinutes(5)
  while ($true) {
    $states = docker compose ps --format "{{.Service}}:{{.Health}}" 2>$null
    $unhealthy = @($states | Where-Object { $_ -and $_ -notmatch ":(healthy|)$" })
    if ($unhealthy.Count -eq 0) { break }
    if ((Get-Date) -gt $deadline) { Die "Containers still not healthy: $($unhealthy -join ', ')" }
    Start-Sleep -Seconds 5
  }
  Note "all services healthy"

  # The other half, and the half that matters. A healthcheck runs *inside* the
  # container, so a service whose host bind failed still reports healthy and
  # `docker compose ps` shows a green stack nothing on the host can reach.
  # That is not hypothetical: WinNAT reserves blocks of ports, and both Temporal
  # (7233) and SQL Server (14330) have silently failed to publish because of it.
  # Comparing what compose declares against what Docker actually published is
  # the entire diagnosis, so it happens here rather than four steps later as
  # "SQL Server did not become reachable".
  Step "      Checking every declared port is actually published"
  $missing = @()
  foreach ($name in (docker compose ps --format "{{.Name}}" 2>$null)) {
    if (-not $name) { continue }
    $declared = docker inspect --format '{{range $p, $c := .HostConfig.PortBindings}}{{$p}} {{end}}' $name 2>$null
    if (-not $declared) { continue }
    $published = docker port $name 2>$null
    if (-not $published) { $missing += $name }
  }
  if ($missing.Count -gt 0) {
    Write-Host "[reset] ERROR: these containers declare ports and published none:" -ForegroundColor Red
    foreach ($m in $missing) { Write-Host "          $m" -ForegroundColor Red }
    Write-Host "        On Windows this is almost always a reserved port range. Check:" -ForegroundColor Yellow
    Write-Host "          netsh interface ipv4 show excludedportrange protocol=tcp" -ForegroundColor Yellow
    Write-Host "        then move the host side of the mapping in compose.yaml and .env." -ForegroundColor Yellow
    exit 1
  }
  Note "every declared port is published"

  # Published is not the same as reachable-by-this-config. A container can
  # publish 17233 while `PLATFORM_TEMPORAL_TARGET` dials 7233, and every process
  # then dies on "connection refused" against a container that is healthy and
  # listening. That has now happened twice, on two datastores.
  Step "      Checking published ports match what the application dials"
  & $python (Join-Path $Root "scripts\preflight_ports.py")
  if ($LASTEXITCODE -ne 0) { Die "Port configuration is inconsistent -- see above." }
} finally {
  Pop-Location
}

# --- 3. Reference dataset ----------------------------------------------------
# Drops every database itself, then loads. Before the host starts, so the
# bootstrap in step 4 recreates the system store it is about to lose.

if (-not $KeepVolumes) {
  Step "3/5  Loading the reference dataset"
  Push-Location $Backend
  try {
    $env:PYTHONPATH = Join-Path $Backend "src"
    $datasetArgs = @()
    if ($Dataset) { $datasetArgs = @("--dataset", $Dataset) }
    & $python (Join-Path $Backend "scripts\load_reference_dataset.py") @datasetArgs
    if ($LASTEXITCODE -ne 0) { Die "Reference dataset load failed." }
  } finally {
    Pop-Location
  }
} else {
  Step "3/5  Skipping dataset load (-KeepVolumes)"
}

# --- 4. Host -----------------------------------------------------------------
# `-NoSupervise`, and without it this script is broken in exactly the way the
# Linux one was: the supervising form never returns, so step 5 is unreachable.

if ($NoHost) {
  Step "4/5  Skipping host start (-NoHost)"
} else {
  Step "4/5  Starting backend, workers and frontend"
  Note "each process reads .env as it starts"
  & powershell -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\run_all_host.ps1") -NoSupervise
  if ($LASTEXITCODE -ne 0) { Die "Host start failed." }
}

# --- 5. Knowledge graph ------------------------------------------------------
# Last, and only after the load: a build against an empty source silently
# produces an empty graph.
#
# The env var raises the second ceiling. `maxRecordsPerAsset` alone cannot get
# past `PLATFORM_GRAPH_SYNC_MAX_RECORDS`, so passing 30000 without it still
# clamps to 10,000 -- exactly the `customers` count in the seed manifest, which
# is no headroom at all.

if ($NoHost -or $KeepVolumes) {
  Step "5/5  Skipping graph build"
  if ($KeepVolumes) { Note "the existing graph is untouched" }
} else {
  Step "5/5  Building the knowledge graph"
  Push-Location $Backend
  try {
    $env:PYTHONPATH = Join-Path $Backend "src"
    $env:PLATFORM_GRAPH_SYNC_MAX_RECORDS = "$GraphRecords"
    & $python (Join-Path $Backend "scripts\build_knowledge_graph.py") $GraphRecords
    if ($LASTEXITCODE -ne 0) { Die "Graph build failed. Neo4j is empty; the copilot will find no orders." }

    # Built is not the same as usable, and the difference is invisible in a build
    # log. A generation can report COMPLETED with order lines that reach no
    # product, or sit behind a search index that cannot see it -- and an empty
    # search is what the discovery agent has been observed answering with
    # invented accounts. Fail here, where the data was produced, rather than in
    # front of an associate holding a box.
    Step "      Verifying the graph can answer a discovery turn"
    & $python (Join-Path $Backend "scripts\verify_graph_ready.py")
    if ($LASTEXITCODE -ne 0) {
      Die "The graph was built but cannot serve -- see the reason above. Do not use this load."
    }
  } finally {
    Pop-Location
  }
}

Write-Host "`n[reset] Done." -ForegroundColor Green
Write-Host "        Confirm the AI routes picked up .env:" -ForegroundColor DarkGray
Write-Host "          backend\.venv\Scripts\python.exe backend\scripts\validate_ai_gateway_live.py" -ForegroundColor DarkGray

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
# Pinned so a host bootstrap and a container build use the same Poetry; keep in
# step with POETRY_VERSION in backend/Dockerfile.
$PoetryVersion = "2.4.1"
$python = Get-Command python3.13 -ErrorAction SilentlyContinue
if (-not $python) {
  $venvPython = Join-Path $Root "backend\.venv\Scripts\python.exe"
  if (Test-Path $venvPython) {
    $python = Get-Command $venvPython
  } else {
    $python = Get-Command python -ErrorAction SilentlyContinue
  }
}
if (-not $python) { throw "Python 3.13 is required." }
$pythonVersion = (& $python.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
if ($pythonVersion -ne "3.13") { throw "Python 3.13 is required; found $pythonVersion." }
$nodeMajor = [int]((node --version).TrimStart('v').Split('.')[0])
if ($nodeMajor -ne 24) { throw "Node.js 24 is required." }
$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npmCommand) { throw "npm 11 is required." }
$npmMajor = [int]((& $npmCommand.Source --version).Split('.')[0])
if ($npmMajor -ne 11) { throw "npm 11 is required." }
if (-not (Test-Path (Join-Path $Root ".env"))) {
  Copy-Item (Join-Path $Root ".env.example") (Join-Path $Root ".env")
  Write-Host "Created .env. Replace placeholder credentials before running services."
}
& $python.Source (Join-Path $Root "scripts\linux\ensure_runtime_env_keys.py") --env-file (Join-Path $Root ".env")
if ($LASTEXITCODE -ne 0) { throw "Runtime environment key preparation failed." }
& $python.Source (Join-Path $Root "scripts\linux\ensure_local_infrastructure_secrets.py")
if ($LASTEXITCODE -ne 0) { throw "Infrastructure secret preparation failed." }
& $python.Source (Join-Path $Root "scripts\linux\ensure_local_replica_key.py")
if ($LASTEXITCODE -ne 0) { throw "MongoDB replica key preparation failed." }
Push-Location (Join-Path $Root "backend")
try {
  # Poetry is the only packaging tool, and poetry.lock the only lockfile. When
  # Poetry is missing we install it rather than falling back to a hand-written
  # `pip install pytest==... ruff==...` line: that line was a second declaration
  # of the dev toolchain, and nothing kept it in step with the lockfile.
  if (Get-Command poetry -ErrorAction SilentlyContinue) {
    $poetry = "poetry"
  } else {
    # Its own venv rather than a user-site install, so nothing is added to the
    # Python that runs the platform.
    $poetryHome = Join-Path $Root ".tmp\poetry"
    if (-not (Test-Path (Join-Path $poetryHome "Scripts\poetry.exe"))) {
      & $python.Source -m venv $poetryHome
      if ($LASTEXITCODE -ne 0) { throw "Poetry virtual environment creation failed." }
      & (Join-Path $poetryHome "Scripts\python.exe") -m pip install --quiet --upgrade pip
      if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
      & (Join-Path $poetryHome "Scripts\python.exe") -m pip install --quiet "poetry==$PoetryVersion"
      if ($LASTEXITCODE -ne 0) { throw "Poetry installation failed." }
    }
    $poetry = (Join-Path $poetryHome "Scripts\poetry.exe")
  }
  & $poetry env use $python.Source
  if ($LASTEXITCODE -ne 0) { throw "Poetry could not select Python 3.13." }
  # `poetry sync`, not `poetry install --sync`: the flag is deprecated in Poetry
  # 2.x. Sync rather than install so a dependency removed from the lockfile is
  # removed from the environment too -- an install-only environment keeps stale
  # packages that mask a missing declaration.
  & $poetry sync
  if ($LASTEXITCODE -ne 0) { throw "Backend dependency synchronization failed." }
} finally { Pop-Location }
Push-Location (Join-Path $Root "frontend")
try {
  $npmCache = Join-Path $Root ".tmp\npm-cache"
  New-Item -ItemType Directory -Path $npmCache -Force | Out-Null
  & $npmCommand.Source ci --cache $npmCache
  if ($LASTEXITCODE -ne 0) { throw "Frontend dependency synchronization failed." }
} finally { Pop-Location }

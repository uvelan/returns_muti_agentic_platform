$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
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
  if (Get-Command uv -ErrorAction SilentlyContinue) {
    $uvCache = Join-Path $Root ".tmp\uv-cache"
    New-Item -ItemType Directory -Path $uvCache -Force | Out-Null
    uv sync --project (Join-Path $Root "backend") --all-groups --frozen --cache-dir $uvCache
    if ($LASTEXITCODE -ne 0) { throw "Backend dependency synchronization failed." }
    uv pip install --python (Join-Path $Root "backend\.venv\Scripts\python.exe") --cache-dir $uvCache pytest==9.1.1 pytest-asyncio==1.4.0 pytest-cov==7.1.0 ruff==0.15.21 mypy==2.3.0 "types-pyyaml>=6.0.12.20260518"
    if ($LASTEXITCODE -ne 0) { throw "Backend development dependency installation failed." }
  } elseif (Get-Command poetry -ErrorAction SilentlyContinue) {
    poetry env use $python.Source
    if ($LASTEXITCODE -ne 0) { throw "Poetry could not select Python 3.13." }
    poetry install --sync
    if ($LASTEXITCODE -ne 0) { throw "Backend dependency synchronization failed." }
  } else {
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
      & $python.Source -m venv .venv
      if ($LASTEXITCODE -ne 0) { throw "Backend virtual environment creation failed." }
    }
    & .\.venv\Scripts\python.exe -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) { throw "pip bootstrap failed." }
    & .\.venv\Scripts\python.exe -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
    & .\.venv\Scripts\python.exe -m pip install -e .
    if ($LASTEXITCODE -ne 0) { throw "Backend dependency installation failed." }
    & .\.venv\Scripts\python.exe -m pip install pytest==9.1.1 pytest-asyncio==1.4.0 pytest-cov==7.1.0 ruff==0.15.21 mypy==2.3.0 "types-pyyaml>=6.0.12.20260518"
    if ($LASTEXITCODE -ne 0) { throw "Backend development dependency installation failed." }
  }
} finally { Pop-Location }
Push-Location (Join-Path $Root "frontend")
try {
  $npmCache = Join-Path $Root ".tmp\npm-cache"
  New-Item -ItemType Directory -Path $npmCache -Force | Out-Null
  & $npmCommand.Source ci --cache $npmCache
  if ($LASTEXITCODE -ne 0) { throw "Frontend dependency synchronization failed." }
} finally { Pop-Location }

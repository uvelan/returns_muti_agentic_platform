$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Get-Command python3.13 -ErrorAction SilentlyContinue
if (-not $python) { throw "Python 3.13 is required." }
$nodeMajor = [int]((node --version).TrimStart('v').Split('.')[0])
if ($nodeMajor -ne 24) { throw "Node.js 24 is required." }
$npmMajor = [int]((npm --version).Split('.')[0])
if ($npmMajor -ne 11) { throw "npm 11 is required." }
if (-not (Test-Path (Join-Path $Root ".env"))) {
  Copy-Item (Join-Path $Root ".env.example") (Join-Path $Root ".env")
  Write-Host "Created .env. Replace placeholder credentials before running services."
}
Push-Location (Join-Path $Root "backend")
try {
  if (Get-Command poetry -ErrorAction SilentlyContinue) {
    poetry env use python3.13
    poetry install --sync
  } else {
    python3.13 -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install --upgrade pip
    & .\.venv\Scripts\python.exe -m pip install -e .
    & .\.venv\Scripts\python.exe -m pip install pytest==9.1.1 pytest-asyncio==1.4.0 pytest-cov==7.1.0 ruff==0.15.21 mypy==2.3.0 "types-pyyaml>=6.0.12.20260518"
  }
} finally { Pop-Location }
Push-Location (Join-Path $Root "frontend")
try { npm ci } finally { Pop-Location }

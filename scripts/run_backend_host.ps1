$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $Root "scripts\vault\export_runtime_vault_env.ps1")
Push-Location (Join-Path $Root "backend")
try {
  $env:PYTHONPATH = (Join-Path $Root "backend\src")
  if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv run uvicorn return_platform.asgi:app --host 0.0.0.0 --port 8000 --reload
  } elseif (Get-Command poetry -ErrorAction SilentlyContinue) {
    poetry run uvicorn return_platform.asgi:app --host 0.0.0.0 --port 8000 --reload
  } else {
    & .\.venv\Scripts\python.exe -m uvicorn return_platform.asgi:app --host 0.0.0.0 --port 8000 --reload
  }
} finally { Pop-Location }

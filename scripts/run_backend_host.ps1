$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location (Join-Path $Root "backend")
try {
  $env:PYTHONPATH = (Join-Path $Root "backend\src")
  if (Get-Command poetry -ErrorAction SilentlyContinue) {
    poetry run uvicorn return_platform.asgi:app --host 0.0.0.0 --port 8000 --reload
  } else {
    & .\.venv\Scripts\python.exe -m uvicorn return_platform.asgi:app --host 0.0.0.0 --port 8000 --reload
  }
} finally { Pop-Location }

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location (Join-Path $Root "backend")
try {
  $env:PYTHONPATH = (Join-Path $Root "backend\src")
  # The configured port, not a literal -- see the note in run_backend_host.sh.
  $BackendPort = if ($env:BACKEND_PORT) { $env:BACKEND_PORT } else { "8000" }
  if (Test-Path -LiteralPath ".\.venv\Scripts\python.exe") {
    & .\.venv\Scripts\python.exe -m uvicorn return_platform.asgi:app --host 0.0.0.0 --port $BackendPort --reload
  } elseif (Get-Command poetry -ErrorAction SilentlyContinue) {
    poetry run uvicorn return_platform.asgi:app --host 0.0.0.0 --port $BackendPort --reload
  } else {
    throw "No backend Python environment is available."
  }
} finally { Pop-Location }

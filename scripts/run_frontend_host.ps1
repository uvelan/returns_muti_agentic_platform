$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location (Join-Path $Root "frontend")
try { npm.cmd run dev } finally { Pop-Location }

[CmdletBinding()]
param(
    [string]$RepositoryRoot = ""
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
$required = @(
    "scripts/linux/lib/common.sh",
    "scripts/linux/run_full_linux_validation.sh",
    "scripts/linux/package_validation_results.sh",
    "scripts/linux/01_verify_transfer.sh",
    "scripts/linux/02_reconstruct_environment.sh",
    "scripts/linux/03_run_backend_quality.sh",
    "scripts/linux/04_run_frontend_quality.sh",
    "scripts/linux/05_run_contract_and_config_checks.sh",
    "scripts/linux/06_start_infrastructure.sh",
    "scripts/linux/07_seed_and_validate_data.sh",
    "scripts/linux/08_start_backend.sh",
    "scripts/linux/09_start_workers.sh",
    "scripts/linux/10_start_frontend.sh",
    "scripts/linux/11_validate_host_processes.sh",
    "scripts/linux/12_validate_worker_heartbeats.sh",
    "scripts/linux/13_run_api_probes.sh",
    "scripts/linux/14_run_real_e2e.sh",
    "scripts/linux/15_collect_failure_evidence.sh",
    "scripts/linux/16_generate_linux_receipt.sh",
    "scripts/linux/17_stop_host_processes.sh",
    "scripts/linux/18_stop_infrastructure.sh",
    "scripts/linux/19_verify_repository_state.sh"
)

$failures = [System.Collections.Generic.List[string]]::new()
foreach ($relative in $required) {
    $path = Join-Path $RepositoryRoot $relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        $failures.Add("Missing required script: $relative")
        continue
    }
    $bytes = [System.IO.File]::ReadAllBytes($path)
    $text = [System.Text.Encoding]::UTF8.GetString($bytes)
    if (-not $text.StartsWith("#!/usr/bin/env bash`n")) {
        $failures.Add("Invalid shebang or non-LF first line: $relative")
    }
    if ($text -notmatch "(?m)^set -euo pipefail$") {
        $failures.Add("Missing strict mode: $relative")
    }
    if ($text.Contains("`r")) {
        $failures.Add("CRLF line endings found: $relative")
    }
    if ($text -match "(?im)\b(TODO|FIXME|PLACEHOLDER)\b") {
        $failures.Add("Incomplete marker found: $relative")
    }
}

$master = Get-Content -Raw -LiteralPath (Join-Path $RepositoryRoot "scripts/linux/run_full_linux_validation.sh")
foreach ($relative in $required | Where-Object { $_ -match "/[0-9]{2}_" }) {
    $name = Split-Path -Leaf $relative
    if ($master -notmatch [regex]::Escape($name)) {
        $failures.Add("Master script does not reference: $name")
    }
}
if ($master -match "(?i)\bcodex\b") {
    $failures.Add("Linux master script must not depend on Codex.")
}

$gitBashPath = Join-Path $env:ProgramFiles "Git\bin\bash.exe"
$bashPath = if (Test-Path -LiteralPath $gitBashPath) {
    $gitBashPath
} else {
    $null
}
if ($null -ne $bashPath) {
    foreach ($relative in $required) {
        & $bashPath -n (Join-Path $RepositoryRoot $relative)
        if ($LASTEXITCODE -ne 0) {
            $failures.Add("Bash syntax failed: $relative")
        }
    }
}

if ($failures.Count -gt 0) {
    $failures | ForEach-Object { Write-Error $_ }
    exit 1
}
Write-Output "Linux kit validation passed ($($required.Count) required scripts)."
if ($null -eq $bashPath) {
    Write-Warning "Git Bash is unavailable; syntax execution is NOT RUN and must run on Linux."
}

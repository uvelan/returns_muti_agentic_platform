[CmdletBinding()]
param(
    [string]$RepositoryRoot = ""
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
$required = @(
    "docs/code_quality/FULL_CODEBASE_REVIEW_FINDINGS.md",
    "docs/code_quality/LINUX_LIVE_VALIDATION_RUNBOOK.md",
    "docs/evidence/code_quality/windows_code_quality_validation.json",
    "docs/evidence/code_quality/linux_validation_handoff.json",
    "docs/evidence/code_quality/windows_to_linux_transfer.json"
)
foreach ($relative in $required) {
    $path = Join-Path $RepositoryRoot $relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required finalization input is missing: $relative"
    }
}
Get-ChildItem -LiteralPath (Join-Path $RepositoryRoot "docs\evidence\code_quality") -Filter "*.json" |
    ForEach-Object { Get-Content -Raw -LiteralPath $_.FullName | ConvertFrom-Json | Out-Null }

& (Join-Path $RepositoryRoot "scripts\windows\validate_linux_kit.ps1") -RepositoryRoot $RepositoryRoot
if ($LASTEXITCODE -ne 0) {
    throw "Linux-kit validation failed."
}
Push-Location $RepositoryRoot
try {
    git diff --check
    if ($LASTEXITCODE -ne 0) {
        throw "Git whitespace validation failed."
    }
    git status --short
    git diff --stat
} finally {
    Pop-Location
}
Write-Output "Final review inputs are structurally valid. No files were staged or committed."

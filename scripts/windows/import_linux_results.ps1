[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ArchivePath,
    [Parameter(Mandatory = $true)][string]$ChecksumPath,
    [string]$RepositoryRoot = ""
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
$archive = (Resolve-Path -LiteralPath $ArchivePath).Path
$checksum = (Resolve-Path -LiteralPath $ChecksumPath).Path
$expected = ((Get-Content -Raw -LiteralPath $checksum).Trim() -split "\s+")[0].ToLowerInvariant()
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
if ($expected -ne $actual) {
    throw "Linux evidence checksum mismatch."
}

$entries = & tar -tzf $archive
if ($LASTEXITCODE -ne 0) {
    throw "The Linux evidence archive is unreadable."
}
foreach ($entry in $entries) {
    if ([System.IO.Path]::IsPathRooted($entry) -or $entry -match "(^|/)\.\.(/|$)") {
        throw "Unsafe archive entry: $entry"
    }
}

$destination = Join-Path $RepositoryRoot ".runtime\imported-linux-results"
New-Item -ItemType Directory -Force -Path $destination | Out-Null
& tar -xzf $archive -C $destination
if ($LASTEXITCODE -ne 0) {
    throw "Linux evidence extraction failed."
}
$receipt = Get-ChildItem -LiteralPath $destination -Recurse -Filter "linux-validation-receipt.json" |
    Select-Object -First 1
if ($null -eq $receipt) {
    throw "Linux validation receipt is missing."
}
$payload = Get-Content -Raw -LiteralPath $receipt.FullName | ConvertFrom-Json
if ($payload.environment -ne "linux" -or $payload.linuxExecutionClaim -ne $true) {
    throw "Receipt does not contain a valid Linux execution claim."
}
$target = Join-Path $RepositoryRoot "docs\evidence\code_quality\linux_validation_handoff.json"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
Copy-Item -LiteralPath $receipt.FullName -Destination $target -Force
Write-Output "Imported verified Linux receipt: $target"
Write-Output "Overall status: $($payload.overallStatus)"

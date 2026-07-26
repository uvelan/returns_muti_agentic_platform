[CmdletBinding()]
param(
    [string]$RepositoryRoot = ""
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}

$utf8 = New-Object System.Text.UTF8Encoding($false)
$artifacts = Join-Path $RepositoryRoot "artifacts"
$manifestPath = Join-Path $RepositoryRoot "docs\evidence\code_quality\windows_to_linux_transfer.json"
New-Item -ItemType Directory -Force -Path $artifacts | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $manifestPath) | Out-Null

Push-Location $RepositoryRoot
try {
    $baseline = (git rev-parse HEAD).Trim()
    $patchLines = [System.Collections.Generic.List[string]]::new()
    git diff --binary HEAD -- . `
        ":(exclude)docs/evidence/code_quality/windows_to_linux_transfer.json" `
        ":(exclude)artifacts/**" |
        ForEach-Object { $patchLines.Add($_) }

    $untracked = git ls-files --others --exclude-standard |
        Where-Object {
            $_ -ne "docs/evidence/code_quality/windows_to_linux_transfer.json" -and
            $_ -notlike "artifacts/*"
    }
    foreach ($relative in $untracked) {
        $oldErrorPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $newFilePatch = git diff --no-index --binary -- /dev/null $relative 2>$null
        $newFileExitCode = $LASTEXITCODE
        $ErrorActionPreference = $oldErrorPreference
        if ($relative.EndsWith(".sh", [System.StringComparison]::OrdinalIgnoreCase)) {
            $newFilePatch = $newFilePatch | ForEach-Object {
                if ($_ -eq "new file mode 100644") { "new file mode 100755" } else { $_ }
            }
        }
        $newFilePatch | ForEach-Object { $patchLines.Add($_) }
        if ($newFileExitCode -notin @(0, 1)) {
            throw "Unable to create patch entry for $relative"
        }
    }

    $patchPath = Join-Path $artifacts "code-quality-review.patch"
    [System.IO.File]::WriteAllText(
        $patchPath,
        (($patchLines -join "`n") + "`n"),
        $utf8
    )
    $patchHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $patchPath).Hash.ToLowerInvariant()
    $patchChecksum = "$patchPath.sha256"
    [System.IO.File]::WriteAllText(
        $patchChecksum,
        "$patchHash  code-quality-review.patch`n",
        $utf8
    )

    git apply --check --reverse --binary $patchPath
    if ($LASTEXITCODE -ne 0) {
        throw "Generated patch does not reverse-apply to the reviewed working tree."
    }

    $timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $manifest = [ordered]@{
        schemaVersion = 1
        baselineCommit = $baseline
        generatedAt = (Get-Date).ToUniversalTime().ToString("o")
        status = "READY_FOR_LINUX"
        patchPath = "artifacts/code-quality-review.patch"
        patchSha256 = $patchHash
        changedFiles = @($patchLines | Where-Object { $_ -like "diff --git *" } |
            ForEach-Object { ($_ -split " b/", 2)[1] })
        linuxExecutionClaim = $false
    }
    [System.IO.File]::WriteAllText(
        $manifestPath,
        (($manifest | ConvertTo-Json -Depth 6) + "`n"),
        $utf8
    )

    $staging = Join-Path $env:TEMP "returns-platform-linux-handoff-$timestamp"
    if (Test-Path -LiteralPath $staging) {
        throw "Refusing to overwrite existing handoff staging directory: $staging"
    }
    New-Item -ItemType Directory -Path $staging | Out-Null
    try {
        $artifactStage = Join-Path $staging "artifacts"
        $evidenceStage = Join-Path $staging "docs\evidence\code_quality"
        $runbookStage = Join-Path $staging "docs\code_quality"
        New-Item -ItemType Directory -Force -Path $artifactStage, $evidenceStage, $runbookStage |
            Out-Null
        Copy-Item -LiteralPath $patchPath, $patchChecksum -Destination $artifactStage
        Copy-Item -LiteralPath $manifestPath -Destination $evidenceStage
        Copy-Item -LiteralPath (Join-Path $RepositoryRoot "docs\code_quality\LINUX_LIVE_VALIDATION_RUNBOOK.md") -Destination $runbookStage
        $archive = Join-Path $artifacts "linux-handoff-$timestamp.zip"
        Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $archive
        $archiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
        $archiveChecksum = "$archive.sha256"
        [System.IO.File]::WriteAllText(
            $archiveChecksum,
            "$archiveHash  $(Split-Path -Leaf $archive)`n",
            $utf8
        )
    } finally {
        Remove-Item -LiteralPath $staging -Recurse -Force
    }

    $manifest.handoffArchivePath = "artifacts/$(Split-Path -Leaf $archive)"
    $manifest.handoffArchiveSha256 = $archiveHash
    [System.IO.File]::WriteAllText(
        $manifestPath,
        (($manifest | ConvertTo-Json -Depth 6) + "`n"),
        $utf8
    )
    Write-Output $patchPath
    Write-Output $patchChecksum
    Write-Output $archive
    Write-Output $archiveChecksum
    Write-Output $manifestPath
} finally {
    Pop-Location
}

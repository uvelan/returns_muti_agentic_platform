[CmdletBinding()]
param(
    [string]$RepositoryRoot = ""
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
$failures = [System.Collections.Generic.List[string]]::new()
$linuxScriptDirectory = Join-Path $RepositoryRoot "scripts/linux"
$allLinuxShellScripts = @(
    Get-ChildItem `
        -LiteralPath $linuxScriptDirectory `
        -File `
        -Filter "*.sh"
)

if (-not $allLinuxShellScripts) {
    $failures.Add(
        "No Linux shell scripts were found in scripts/linux"
    )
}

foreach ($script in $allLinuxShellScripts) {
    $content = Get-Content -Raw -LiteralPath $script.FullName
    $relativePath = $script.FullName.Substring(
        $RepositoryRoot.Length
    ).TrimStart('\', '/').Replace('\', '/')
    $stageEntry = git -C $RepositoryRoot ls-files --stage -- $relativePath

    if (
        $LASTEXITCODE -ne 0 -or
        $stageEntry -notmatch '^100755\s'
    ) {
        $failures.Add(
            "Linux script is not executable in Git (expected mode 100755): " +
            $script.Name
        )
    }

    if ($content -notmatch '^#!/usr/bin/env bash') {
        $failures.Add(
            "Linux script has an invalid or missing Bash shebang: " +
            $script.Name
        )
    }

    if (
        $content -notmatch
        '(?m)^set -(?:E)?euo pipefail\s*$'
    ) {
        $failures.Add(
            "Linux script does not enable strict Bash mode: " +
            $script.Name
        )
    }

    $bytes = [System.IO.File]::ReadAllBytes($script.FullName)
    $text = [System.Text.Encoding]::UTF8.GetString($bytes)

    if ($text.Contains("`r`n")) {
        $failures.Add(
            "Linux script contains CRLF line endings: " +
            $script.Name
        )
    }

    if ($text -match "(?im)\b(TODO|FIXME|PLACEHOLDER)\b") {
        $failures.Add(
            "Incomplete marker found: " +
            $script.Name
        )
    }

    $gitBashPath = Join-Path $env:ProgramFiles "Git\bin\bash.exe"
    if (Test-Path -LiteralPath $gitBashPath) {
        & $gitBashPath -n $script.FullName
        if ($LASTEXITCODE -ne 0) {
            $failures.Add(
                "Bash syntax validation failed: " +
                $script.Name
            )
        }
    } else {
        $forwardPath = $script.FullName.Replace('\', '/')
        $wslScriptPathResult = wsl wslpath -a "$forwardPath" 2>$null
        if ($LASTEXITCODE -ne 0 -or $null -eq $wslScriptPathResult) {
            $failures.Add(
                "Unable to resolve WSL path for Linux script: " +
                $script.Name
            )
            continue
        }
        $wslScriptPath = [string]$wslScriptPathResult
        wsl bash -n "$($wslScriptPath.Trim())"
        if ($LASTEXITCODE -ne 0) {
            $failures.Add(
                "Bash syntax validation failed: " +
                $script.Name
            )
        }
    }
}

$masterPath = Join-Path $linuxScriptDirectory "run_full_linux_validation.sh"
$manifestPath = Join-Path $linuxScriptDirectory "validation_phases.txt"

$expectedExecutionPhaseCount = 21

$requiredUtilityNames = @(
    "15_collect_failure_evidence.sh",
    "16_generate_linux_receipt.sh",
    "17_stop_host_processes.sh",
    "18_stop_infrastructure.sh"
)

# ---------------------------------------------------------------------------
# Validate the master runner
# ---------------------------------------------------------------------------

if (-not (Test-Path -LiteralPath $masterPath -PathType Leaf)) {
    $failures.Add(
        "Missing Linux validation master script: $masterPath"
    )
}
else {
    $masterContent = Get-Content -Raw -LiteralPath $masterPath

    if ($masterContent -notmatch [regex]::Escape("validation_phases.txt")) {
        $failures.Add(
            "Linux validation master does not reference validation_phases.txt"
        )
    }

    if ($masterContent -notmatch 'readarray\s+-t\s+phases') {
        $failures.Add(
            "Linux validation master does not load phases with readarray"
        )
    }

    $phaseLoopPattern = @'
for\s+[A-Za-z_][A-Za-z0-9_]*\s+in\s+"\$\{phases\[@\]\}"\s*;\s*do
'@.Trim()

    if ($masterContent -notmatch $phaseLoopPattern) {
        $failures.Add(
            "Linux validation master does not execute phases in manifest order"
        )
    }

    if ($masterContent -match "(?i)\bcodex\b") {
        $failures.Add("Linux master script must not depend on Codex.")
    }
}

# ---------------------------------------------------------------------------
# Read and validate the execution manifest
# ---------------------------------------------------------------------------

$manifestEntries = @()

if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    $failures.Add(
        "Missing Linux validation phase manifest: $manifestPath"
    )
}
else {
    $manifestEntries = @(
        Get-Content -LiteralPath $manifestPath |
            ForEach-Object {
                $_.Trim()
            } |
            Where-Object {
                $_ -and
                -not $_.StartsWith("#")
            }
    )
}

if ($manifestEntries.Count -ne $expectedExecutionPhaseCount) {
    $failures.Add(
        "Phase manifest count mismatch. " +
        "Expected $expectedExecutionPhaseCount, " +
        "found $($manifestEntries.Count)"
    )
}

$manifestPhaseNames = [System.Collections.Generic.List[string]]::new()

foreach ($entry in $manifestEntries) {
    $normalizedEntry = $entry.Replace("\", "/")

    if ([System.IO.Path]::IsPathRooted($normalizedEntry)) {
        $failures.Add(
            "Absolute path is not allowed in validation_phases.txt: $entry"
        )
        continue
    }

    if ($normalizedEntry -match '(^|/)\.\.(/|$)') {
        $failures.Add(
            "Directory traversal is not allowed in validation_phases.txt: $entry"
        )
        continue
    }

    if (
        $normalizedEntry -notmatch
        '^[0-9]{2}_[A-Za-z0-9_.-]+\.sh$'
    ) {
        $failures.Add(
            "Invalid phase entry format in validation_phases.txt: $entry"
        )
        continue
    }

    $manifestPhaseNames.Add($normalizedEntry)

    $phasePath = Join-Path $linuxScriptDirectory $normalizedEntry

    if (-not (Test-Path -LiteralPath $phasePath -PathType Leaf)) {
        $failures.Add(
            "Phase manifest references a missing script: $entry"
        )
    }
}

# ---------------------------------------------------------------------------
# Detect duplicate manifest entries
# ---------------------------------------------------------------------------

$duplicateManifestEntries = @(
    $manifestPhaseNames |
        Group-Object |
        Where-Object {
            $_.Count -gt 1
        }
)

foreach ($duplicate in $duplicateManifestEntries) {
    $failures.Add(
        "Duplicate phase in validation_phases.txt: $($duplicate.Name)"
    )
}

# ---------------------------------------------------------------------------
# Validate required utility scripts
# ---------------------------------------------------------------------------

foreach ($utilityName in $requiredUtilityNames) {
    $utilityPath = Join-Path $linuxScriptDirectory $utilityName

    if (-not (Test-Path -LiteralPath $utilityPath -PathType Leaf)) {
        $failures.Add(
            "Missing required Linux utility script: $utilityName"
        )
    }

    if ($utilityName -in $manifestPhaseNames) {
        $failures.Add(
            "Utility script must not appear in validation_phases.txt: " +
            $utilityName
        )
    }
}

# ---------------------------------------------------------------------------
# Compare manifest against executable phase scripts on disk
# ---------------------------------------------------------------------------

$numberedScriptsOnDisk = @(
    Get-ChildItem `
        -LiteralPath $linuxScriptDirectory `
        -File `
        -Filter "*.sh" |
        Where-Object {
            $_.Name -match '^[0-9]{2}_.+\.sh$'
        } |
        Select-Object -ExpandProperty Name
)

$expectedExecutionPhaseNames = @(
    $numberedScriptsOnDisk |
        Where-Object {
            $_ -notin $requiredUtilityNames
        }
)

if (
    $expectedExecutionPhaseNames.Count -ne
    $expectedExecutionPhaseCount
) {
    $failures.Add(
        "Executable phase count on disk is unexpected. " +
        "Expected $expectedExecutionPhaseCount, " +
        "found $($expectedExecutionPhaseNames.Count)"
    )
}

$missingFromManifest = @(
    $expectedExecutionPhaseNames |
        Where-Object {
            $_ -notin $manifestPhaseNames
        }
)

foreach ($missingName in $missingFromManifest) {
    $failures.Add(
        "Execution phase is missing from validation_phases.txt: " +
        $missingName
    )
}

$unexpectedManifestEntries = @(
    $manifestPhaseNames |
        Where-Object {
            $_ -notin $expectedExecutionPhaseNames
        }
)

foreach ($unexpectedName in $unexpectedManifestEntries) {
    $failures.Add(
        "Unexpected execution phase in validation_phases.txt: " +
        $unexpectedName
    )
}

# ---------------------------------------------------------------------------
# Validate the intentional final workflow order
# ---------------------------------------------------------------------------

$manualAttestationIndex = [Array]::IndexOf(
    [string[]]$manifestPhaseNames,
    "20_verify_manual_screen_attestation.sh"
)

$repositoryStateIndex = [Array]::IndexOf(
    [string[]]$manifestPhaseNames,
    "19_verify_repository_state.sh"
)

if ($manualAttestationIndex -lt 0) {
    $failures.Add(
        "Manifest is missing 20_verify_manual_screen_attestation.sh"
    )
}

if ($repositoryStateIndex -lt 0) {
    $failures.Add(
        "Manifest is missing 19_verify_repository_state.sh"
    )
}

if (
    $manualAttestationIndex -ge 0 -and
    $repositoryStateIndex -ge 0 -and
    $manualAttestationIndex -gt $repositoryStateIndex
) {
    $failures.Add(
        "20_verify_manual_screen_attestation.sh must execute before " +
        "19_verify_repository_state.sh"
    )
}

if ($failures.Count -gt 0) {
    $failures | ForEach-Object { Write-Error $_ }
    exit 1
}

Write-Output "Linux kit validation passed ($($allLinuxShellScripts.Count) Linux shell scripts checked, $expectedExecutionPhaseCount execution phases confirmed)."

<#
.SYNOPSIS
  Copies an extracted build package into the repository without overwriting anything silently.

.DESCRIPTION
  1. Verifies every file in the package against .package\MANIFEST.txt (SHA-256).
  2. Compares each file with the repository copy: NEW, SAME or CHANGED.
  3. If any file would be CHANGED and -Force is not set, nothing is copied and the conflicts are
     listed so they can be reviewed (for example work by the other build chat).
  4. With -Force, changed files are first backed up to <repo>\.forge-backups\<timestamp>.
  Use -DryRun to see the plan without copying.

.EXAMPLE
  .\install-package.ps1 -Source C:\Users\HP\Downloads\forge-day1-core-platform -DryRun
  .\install-package.ps1 -Source C:\Users\HP\Downloads\forge-day1-core-platform
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Source,
    [string]$Target = "C:\Projects\forge",
    [switch]$DryRun,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")

if (-not (Test-Path -LiteralPath $Source)) { throw "Source folder not found: $Source" }
if (-not (Test-Path -LiteralPath $Target)) { throw "Target folder not found: $Target. Clone the repository first." }
$Source = (Resolve-Path -LiteralPath $Source).Path
$Target = (Resolve-Path -LiteralPath $Target).Path
$manifestPath = Join-Path (Join-Path $Source '.package') 'MANIFEST.txt'
if (-not (Test-Path -LiteralPath $manifestPath)) { throw "MANIFEST.txt not found at $manifestPath. Is this an extracted Forge package?" }

Write-Step "Verifying package integrity"
$entries = New-Object System.Collections.Generic.List[object]
foreach ($line in [System.IO.File]::ReadAllLines($manifestPath)) {
    if ($line.Trim() -eq '' -or $line.StartsWith('#')) { continue }
    $parts = $line -split '  ', 2
    if ($parts.Count -ne 2) { throw "Malformed manifest line: $line" }
    $entries.Add(@{ Hash = $parts[0].Trim().ToLower(); Relative = $parts[1].Trim() })
}
$corrupt = 0
foreach ($entry in $entries) {
    $path = Join-Path $Source ($entry.Relative -replace '/', '\')
    if (-not (Test-Path -LiteralPath $path)) { Write-Fail "missing in package: $($entry.Relative)"; $corrupt++; continue }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLower()
    if ($actual -ne $entry.Hash) { Write-Fail "checksum mismatch: $($entry.Relative)"; $corrupt++ }
}
if ($corrupt -gt 0) { throw "$corrupt file(s) failed verification. Extract the ZIP again and retry." }
Write-Ok "$($entries.Count) files verified"

Write-Step "Comparing with $Target"
$plan = New-Object System.Collections.Generic.List[object]
foreach ($entry in $entries) {
    $relativeWindows = $entry.Relative -replace '/', '\'
    $destination = Join-Path $Target $relativeWindows
    $state = 'NEW'
    if (Test-Path -LiteralPath $destination) {
        $existing = (Get-FileHash -Algorithm SHA256 -LiteralPath $destination).Hash.ToLower()
        if ($existing -eq $entry.Hash) { $state = 'SAME' } else { $state = 'CHANGED' }
    }
    $plan.Add(@{ Relative = $relativeWindows; Source = (Join-Path $Source $relativeWindows); Destination = $destination; State = $state })
}
$new = @($plan | Where-Object { $_.State -eq 'NEW' })
$same = @($plan | Where-Object { $_.State -eq 'SAME' })
$changed = @($plan | Where-Object { $_.State -eq 'CHANGED' })
Write-Info "new: $($new.Count)   unchanged: $($same.Count)   would overwrite: $($changed.Count)"
foreach ($item in $changed) { Write-Warn2 "would overwrite: $($item.Relative)" }

if ($changed.Count -gt 0 -and -not $Force) {
    Write-Host ""
    Write-Fail "Nothing was copied. $($changed.Count) existing file(s) differ from the package."
    Write-Info "Review them (git diff, or compare with the extracted copy). If the package should win,"
    Write-Info "run again with -Force. Existing files are backed up before they are replaced."
    exit 2
}
if ($DryRun) {
    Write-Host ""
    Write-Ok "Dry run complete. Nothing was copied."
    exit 0
}

Write-Step "Copying"
if ($changed.Count -gt 0) {
    $backupRoot = Join-Path (Join-Path $Target '.forge-backups') (Get-Date -Format 'yyyyMMdd-HHmmss')
    foreach ($item in $changed) {
        $backupPath = Join-Path $backupRoot $item.Relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backupPath) | Out-Null
        Copy-Item -LiteralPath $item.Destination -Destination $backupPath -Force
    }
    Write-Ok "backed up $($changed.Count) file(s) to $backupRoot"
}
$copied = 0
foreach ($item in ($new + $changed)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $item.Destination) | Out-Null
    Copy-Item -LiteralPath $item.Source -Destination $item.Destination -Force
    $copied++
}
Write-Ok "copied $copied file(s) into $Target"
Write-Info "Next: review with 'git status' and 'git diff', then commit on a feature branch."

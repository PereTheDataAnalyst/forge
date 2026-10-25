<#
.SYNOPSIS
  Tests whether the MCP SDK can run next to the exact package versions the production build installs.

.DESCRIPTION
  The MCP server in integrations\ needs the official "mcp" SDK. The live image installs the exact
  versions in backend\constraints.txt, and a deploy happens on every push to main, so the SDK
  must be proven compatible with those versions BEFORE a change to them is merged.

  This script builds a throwaway virtual environment outside the repository, installs the
  library with its "mcp" extra and the backend together in ONE resolver run under
  backend\constraints.txt (the same file the Dockerfile uses), runs "pip check" and the library's
  MCP tests, and writes a report. The verdict rests on those three things.

  It then runs backend\tests\test_mcp_embedded.py, which serves the real SDK through the
  platform's /mcp mount. That result is reported as evidence only and does not change the verdict:
  the test was written without the SDK available to run it against.

  It changes nothing in the repository and nothing in backend\.venv. It needs internet access to
  PyPI.

.EXAMPLE
  .\scripts\probe-mcp-compat.ps1
#>
[CmdletBinding()]
param(
    [string]$RepoPath = (Split-Path -Parent $PSScriptRoot),
    [string]$WorkDir = "C:\Projects\forge-probe",
    [string]$ReportPath = "C:\Projects\forge-evidence\mcp-probe.txt"
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "lib\common.ps1")

$RepoPath = (Resolve-Path -LiteralPath $RepoPath).Path
$integrations = Join-Path $RepoPath "integrations"
$backend = Join-Path $RepoPath "backend"
$venv = Join-Path $WorkDir "venv"
# One list of exact versions for the image and for this probe, so they cannot drift apart.
$constraints = Join-Path $backend "constraints.txt"
$report = New-Object System.Collections.Generic.List[string]

function Add-Report([string]$Text) {
    $report.Add($Text)
    Write-Info $Text
}

Write-Step "Preparing a throwaway environment in $WorkDir"
if (-not (Test-Path -LiteralPath $constraints)) {
    Write-Fail "backend\constraints.txt was not found. It lists the exact versions to test."
    exit 1
}
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
if (Test-Path -LiteralPath $venv) { Remove-Item -LiteralPath $venv -Recurse -Force }
$basePython = Get-VenvPython $backend
if (-not (Test-Path -LiteralPath $basePython)) {
    Write-Fail "backend\.venv was not found. Run .\scripts\setup-local.ps1 first."
    exit 1
}
& $basePython -m venv $venv
$python = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    Write-Fail "The throwaway environment could not be created."
    exit 1
}
$pythonVersion = (& $python --version 2>&1 | Out-String).Trim()
Add-Report "python: $pythonVersion (the production image runs Python 3.12)"
& $python -m pip install --upgrade pip 2>&1 | Out-Null

Write-Step "Installing the library with the mcp extra and the backend, under backend\constraints.txt"
$extraSpec = $integrations + "[mcp,dev]"
$installOutput = & $python -m pip install -c $constraints -e $extraSpec $backend 2>&1
$installExit = $LASTEXITCODE
$installOutput | Select-Object -Last 20 | ForEach-Object { Write-Info ([string]$_) }

$checkExit = 1
$testsExit = 1
$embedStatus = "not run"
if ($installExit -eq 0) {
    Write-Step "pip check"
    $checkOutput = & $python -m pip check 2>&1
    $checkExit = $LASTEXITCODE
    $checkOutput | ForEach-Object { Write-Info ([string]$_) }

    Write-Step "The library's MCP tests"
    Push-Location $integrations
    $testOutput = & $python -m unittest discover -s tests -t . -p "test_mcp_*.py" 2>&1
    $testsExit = $LASTEXITCODE
    Pop-Location
    $testOutput | Select-Object -Last 15 | ForEach-Object { Write-Info ([string]$_) }

    Write-Step "Embedding check: the real SDK served through the platform's /mcp mount (evidence only)"
    Push-Location $backend
    $embedOutput = & $python -m pytest tests/test_mcp_embedded.py -q -p no:cacheprovider 2>&1
    $embedExit = $LASTEXITCODE
    Pop-Location
    $embedOutput | Select-Object -Last 20 | ForEach-Object { Write-Info ([string]$_) }
    $embedText = ($embedOutput | Out-String)
    if ($embedExit -eq 0 -and $embedText -match "passed") {
        $embedStatus = "PASSED"
    } elseif ($embedExit -eq 0) {
        $embedStatus = "SKIPPED (the SDK was not importable)"
    } else {
        $embedStatus = "FAILED. Read the output above. The test was written without the SDK to run it against, so a failure can be a fault in the test or in the mount: report it, do not guess."
    }
}

Write-Step "Versions in the throwaway environment"
$wanted = "^(mcp|mcp-types|starlette|fastapi|pydantic|pydantic-core|pydantic-settings|httpx|httpx2|httpcore2|anyio|uvicorn|sse-starlette|jsonschema|attrs|referencing|rpds-py|jsonschema-specifications|pyjwt|cryptography|python-multipart|truststore)=="
$frozen = & $python -m pip list --format=freeze 2>&1
foreach ($line in $frozen) {
    if ([string]$line -match $wanted) { Add-Report ([string]$line) }
}

if ($installExit -ne 0) {
    $verdict = "INCOMPATIBLE: the resolver could not install the MCP SDK under backend\constraints.txt. Do not merge a change to the image."
} elseif ($checkExit -ne 0) {
    $verdict = "CONFLICTS: pip check reports broken requirements. Do not add the SDK to the image yet."
} elseif ($testsExit -ne 0) {
    $verdict = "The SDK installs, but the library's MCP tests fail here. Read the output above before wiring MCP."
} else {
    $verdict = "COMPATIBLE: the MCP SDK installs under backend\constraints.txt, pip check is clean and the library's MCP tests pass."
}
Add-Report "embedding check (evidence only, not part of the verdict): $embedStatus"
Add-Report "verdict: $verdict"

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ReportPath) | Out-Null
Set-Content -LiteralPath $ReportPath -Value $report -Encoding ASCII
Write-Step "Report written"
Write-Info $ReportPath
if ($verdict -like "COMPATIBLE*") { Write-Ok $verdict; exit 0 }
Write-Fail $verdict
exit 1

<#
.SYNOPSIS
  Tests whether the MCP SDK can run next to the exact package versions the production build installs.

.DESCRIPTION
  The MCP server in integrations\ needs the official "mcp" SDK. The live image installs fixed
  versions of FastAPI, Starlette and pydantic, and a deploy happens on every push to main, so the
  SDK must be proven compatible BEFORE it is added to the image.

  This script builds a throwaway virtual environment outside the repository, installs the
  library with its "mcp" extra and the backend together in ONE resolver run under constraints
  that pin the versions of the live build, runs "pip check" and the library's MCP tests, and
  writes a report. It changes nothing in the repository and nothing in backend\.venv. It needs
  internet access to PyPI.

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
$constraints = Join-Path $WorkDir "constraints.txt"
$report = New-Object System.Collections.Generic.List[string]

function Add-Report([string]$Text) {
    $report.Add($Text)
    Write-Info $Text
}

# The versions the live build installed (see its build log). Everything else may float.
$pins = @(
    "fastapi==0.143.2", "starlette==1.8.0", "pydantic==2.14.1", "pydantic-core==2.50.1",
    "pydantic-settings==2.16.0", "httpx==0.28.1", "uvicorn==0.54.0", "anyio==4.15.1",
    "sqlalchemy==2.0.54", "asyncpg==0.32.0", "alembic==1.20.0", "redis==6.4.0",
    "argon2-cffi==25.1.0", "typing-extensions==4.16.0"
)

Write-Step "Preparing a throwaway environment in $WorkDir"
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
Set-Content -LiteralPath $constraints -Value $pins -Encoding ASCII
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

Write-Step "Installing the library with the mcp extra and the backend, under the live versions"
$extraSpec = $integrations + "[mcp,dev]"
$installOutput = & $python -m pip install -c $constraints -e $extraSpec $backend 2>&1
$installExit = $LASTEXITCODE
$installOutput | Select-Object -Last 20 | ForEach-Object { Write-Info ([string]$_) }

$checkExit = 1
$testsExit = 1
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
}

Write-Step "Versions in the throwaway environment"
$wanted = "^(mcp|starlette|fastapi|pydantic|pydantic-core|pydantic-settings|httpx|anyio|uvicorn|sse-starlette|jsonschema|pyjwt|cryptography)=="
$frozen = & $python -m pip list --format=freeze 2>&1
foreach ($line in $frozen) {
    if ([string]$line -match $wanted) { Add-Report ([string]$line) }
}

if ($installExit -ne 0) {
    $verdict = "INCOMPATIBLE: the resolver could not install the MCP SDK next to the live versions. Do not add it to the image."
} elseif ($checkExit -ne 0) {
    $verdict = "CONFLICTS: pip check reports broken requirements. Do not add the SDK to the image yet."
} elseif ($testsExit -ne 0) {
    $verdict = "The SDK installs, but the library's MCP tests fail here. Read the output above before wiring MCP."
} else {
    $verdict = "COMPATIBLE: the MCP SDK installs next to the live versions, pip check is clean and the MCP tests pass."
}
Add-Report "verdict: $verdict"

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ReportPath) | Out-Null
Set-Content -LiteralPath $ReportPath -Value $report -Encoding ASCII
Write-Step "Report written"
Write-Info $ReportPath
if ($verdict -like "COMPATIBLE*") { Write-Ok $verdict; exit 0 }
Write-Fail $verdict
exit 1

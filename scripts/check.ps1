<#
.SYNOPSIS
  Runs the quality gates: lint, migration parity, tests, type check and production build.

.DESCRIPTION
  Backend:  ruff, alembic check (models match migrations), pytest (integration tests use the
            dedicated test database and Redis logical database 15 from .env).
  Worker:   pytest, only when the worker environment exists.
  Frontend: TypeScript type check, ESLint and the production build.
  Exits with code 1 if any gate fails.

.EXAMPLE
  .\scripts\check.ps1
  .\scripts\check.ps1 -SkipFrontend
  .\scripts\check.ps1 -WithAudit
#>
[CmdletBinding()]
param(
    [string]$RepoPath = (Split-Path -Parent $PSScriptRoot),
    [switch]$SkipBackend,
    [switch]$SkipFrontend,
    [switch]$WithAudit
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "lib\common.ps1")

$RepoPath = (Resolve-Path -LiteralPath $RepoPath).Path
$backend = Join-Path $RepoPath "backend"
$frontend = Join-Path $RepoPath "frontend"
$worker = Join-Path (Join-Path $RepoPath "inference") "natlas-worker"
$envFile = Join-Path $RepoPath ".env"
$results = New-Object System.Collections.Generic.List[object]

function Invoke-Gate([string]$Name, [string]$Directory, [string]$Exe, [string[]]$Arguments) {
    Write-Step $Name
    $code = 1
    Push-Location -LiteralPath $Directory
    try {
        & $Exe @Arguments
        $code = $LASTEXITCODE
    } catch {
        Write-Fail $_.Exception.Message
        $code = 1
    } finally {
        Pop-Location
    }
    if ($code -eq 0) { Write-Ok "passed" } else { Write-Fail "failed with exit code $code" }
    $results.Add(@{ Name = $Name; Passed = ($code -eq 0) })
}

$setTestDb = $false
$setTestRedis = $false
try {
    if (-not $SkipBackend) {
        $venvPython = Get-VenvPython $backend
        if (-not (Test-Path -LiteralPath $venvPython)) {
            Write-Fail "backend\.venv was not found. Run .\scripts\setup-local.ps1 first."
            $results.Add(@{ Name = "Backend environment"; Passed = $false })
        } else {
            $testDb = Get-EnvValue $envFile "TEST_DATABASE_URL"
            $testRedis = Get-EnvValue $envFile "TEST_REDIS_URL"
            if ($testDb) { $env:TEST_DATABASE_URL = $testDb; $setTestDb = $true }
            if ($testRedis) { $env:TEST_REDIS_URL = $testRedis; $setTestRedis = $true }
            if (-not $env:TEST_DATABASE_URL) { Write-Warn2 "TEST_DATABASE_URL is not set, so integration tests will be skipped." }

            Invoke-Gate "Backend: ruff lint" $backend $venvPython @("-m", "ruff", "check", ".")
            Invoke-Gate "Backend: migrations match models (alembic check)" $backend $venvPython @("-m", "alembic", "check")
            Invoke-Gate "Backend: tests (pytest)" $backend $venvPython @("-m", "pytest", "-q")
        }
        $workerPython = Get-VenvPython $worker
        if (Test-Path -LiteralPath $workerPython) {
            Invoke-Gate "Worker: tests (pytest)" $worker $workerPython @("-m", "pytest", "-q")
        } else {
            Write-Host ""
            Write-Info "Worker tests skipped (create the environment with .\scripts\setup-local.ps1 -WithWorker)."
        }
    }

    if (-not $SkipFrontend) {
        if (-not (Test-Path -LiteralPath (Join-Path $frontend "node_modules"))) {
            Write-Fail "frontend\node_modules was not found. Run .\scripts\setup-local.ps1 first."
            $results.Add(@{ Name = "Frontend environment"; Passed = $false })
        } else {
            Invoke-Gate "Frontend: type check" $frontend "npm" @("run", "typecheck")
            Invoke-Gate "Frontend: lint" $frontend "npm" @("run", "lint")
            Invoke-Gate "Frontend: production build" $frontend "npm" @("run", "build")
            if ($WithAudit) {
                Invoke-Gate "Frontend: dependency audit (high and above)" $frontend "npm" @("audit", "--audit-level=high")
            }
        }
    }
} finally {
    if ($setTestDb) { Remove-Item Env:\TEST_DATABASE_URL -ErrorAction SilentlyContinue }
    if ($setTestRedis) { Remove-Item Env:\TEST_REDIS_URL -ErrorAction SilentlyContinue }
}

Write-Host ""
Write-Host "Summary" -ForegroundColor Cyan
foreach ($result in $results) {
    $mark = if ($result.Passed) { "PASS" } else { "FAIL" }
    $color = if ($result.Passed) { "Green" } else { "Red" }
    Write-Host ("  {0}  {1}" -f $mark, $result.Name) -ForegroundColor $color
}
$failed = @($results | Where-Object { -not $_.Passed }).Count
if ($failed -gt 0) {
    Write-Host ""
    Write-Host "$failed gate(s) failed." -ForegroundColor Red
    exit 1
}
Write-Host ""
Write-Host "All gates passed." -ForegroundColor Green
exit 0

<#
.SYNOPSIS
  Prepares a local N-ATLAS Forge development environment. Safe to run repeatedly.

.DESCRIPTION
  - Creates .env from .env.example with freshly generated secrets. An existing .env is never replaced.
  - Creates the PostgreSQL role forge_app and the databases forge_dev and forge_test if they are
    missing. Existing databases are never dropped or modified.
  - Checks that Redis (Memurai) is reachable.
  - Creates backend\.venv, installs the backend, and applies database migrations.
  - Installs the frontend dependencies.
  - With -WithWorker, prepares the N-ATLAS inference worker environment (you install PyTorch).

.EXAMPLE
  .\scripts\setup-local.ps1
  .\scripts\setup-local.ps1 -WithWorker
#>
[CmdletBinding()]
param(
    [string]$RepoPath = (Split-Path -Parent $PSScriptRoot),
    [switch]$SkipDatabase,
    [switch]$SkipFrontend,
    [switch]$WithWorker,
    [string]$PgAdminUser = "postgres",
    [int]$RedisPort = 6379
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")

$RepoPath = (Resolve-Path -LiteralPath $RepoPath).Path
$backend = Join-Path $RepoPath "backend"
$frontend = Join-Path $RepoPath "frontend"
$envFile = Join-Path $RepoPath ".env"
$envExample = Join-Path $RepoPath ".env.example"

Write-Step "Checking tools"
$python = Find-Python
if ($null -eq $python) {
    throw "Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/ and tick 'Add python.exe to PATH'."
}
Write-Ok "Python $($python.Version)"
if ($null -eq (Get-Command node -ErrorAction SilentlyContinue)) { throw "Node.js was not found. Install the LTS version from https://nodejs.org/." }
$nodeVersion = ((& node --version) -join '').Trim()
$nodeMajor = [int](($nodeVersion -replace '^v', '').Split('.')[0])
if ($nodeMajor -lt 20) { throw "Node.js 20 or newer is required (found $nodeVersion)." }
Write-Ok "Node.js $nodeVersion"
if ($null -eq (Get-Command npm -ErrorAction SilentlyContinue)) { throw "npm was not found." }

Write-Step "Checking Redis (Memurai)"
if (Test-TcpPort "localhost" $RedisPort) {
    Write-Ok "Redis answers on localhost:$RedisPort"
} else {
    Write-Warn2 "Nothing is listening on localhost:$RedisPort. Start Memurai before running the backend."
    Write-Info "For example: Start-Service Memurai   (run PowerShell as Administrator)"
}

Write-Step "Environment file"
if (Test-Path -LiteralPath $envFile) {
    Write-Ok ".env already exists and was left untouched"
} else {
    if (-not (Test-Path -LiteralPath $envExample)) { throw ".env.example is missing from $RepoPath." }
    Copy-Item -LiteralPath $envExample -Destination $envFile
    $generatedPassword = New-AlphaNumericSecret 32
    Set-EnvValue $envFile "SECRET_KEY" (New-UrlSafeSecret 48)
    Set-EnvValue $envFile "API_KEY_PEPPER" (New-UrlSafeSecret 48)
    Set-EnvValue $envFile "DATABASE_URL" "postgresql+asyncpg://forge_app:${generatedPassword}@localhost:5432/forge_dev"
    Set-EnvValue $envFile "TEST_DATABASE_URL" "postgresql+asyncpg://forge_app:${generatedPassword}@localhost:5432/forge_test"
    Write-Ok ".env created with generated secrets (Git ignores it)"
}

if (-not $SkipDatabase) {
    Write-Step "PostgreSQL"
    $psql = Find-Psql
    if ($null -eq $psql) {
        throw "psql.exe was not found. Add the PostgreSQL bin folder to PATH, or rerun with -SkipDatabase if the databases already exist."
    }
    $pattern = '^postgresql\+asyncpg://([^:]+):([^@]+)@([^:/]+):(\d+)/([^/?]+)$'
    $databaseUrl = Get-EnvValue $envFile "DATABASE_URL"
    if ($databaseUrl -notmatch $pattern) { throw "DATABASE_URL in .env is not in the form postgresql+asyncpg://user:password@host:port/database." }
    $dbUser = $Matches[1]
    $dbPassword = $Matches[2]
    $dbHost = $Matches[3]
    $dbPort = [int]$Matches[4]
    $devDatabase = $Matches[5]
    $testDatabase = "forge_test"
    $testUrl = Get-EnvValue $envFile "TEST_DATABASE_URL"
    if ($null -ne $testUrl -and $testUrl -match $pattern) { $testDatabase = $Matches[5] }
    if ($testDatabase -notmatch 'test') { throw "The test database name '$testDatabase' must contain 'test'. The test suite refuses to run otherwise." }

    function Test-AppConnection([string]$Database) {
        $env:PGPASSWORD = $dbPassword
        try {
            $result = Invoke-Psql $psql @("-h", $dbHost, "-p", "$dbPort", "-U", $dbUser, "-d", $Database, "-tA", "-c", "SELECT 1")
            return ($result.ExitCode -eq 0 -and $result.Output -eq "1")
        } finally {
            Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
        }
    }

    if ((Test-AppConnection $devDatabase) -and (Test-AppConnection $testDatabase)) {
        Write-Ok "'$devDatabase' and '$testDatabase' are reachable as '$dbUser'"
    } else {
        Write-Info "Administrator access is needed once to create the role and databases."
        $adminPassword = Read-PlainPassword "Password for PostgreSQL user '$PgAdminUser'"
        $env:PGPASSWORD = $adminPassword
        try {
            $baseArgs = @("-h", $dbHost, "-p", "$dbPort", "-U", $PgAdminUser, "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-tA")
            $probe = Invoke-Psql $psql ($baseArgs + @("-c", "SELECT 1"))
            if ($probe.ExitCode -ne 0) { throw "Could not connect to PostgreSQL as '$PgAdminUser'. Check the password and that the service is running." }

            $role = Invoke-Psql $psql ($baseArgs + @("-c", "SELECT 1 FROM pg_roles WHERE rolname='$dbUser'"))
            if ($role.Output -eq "1") {
                $roleSql = "ALTER ROLE $dbUser WITH LOGIN PASSWORD '$dbPassword';"
                Write-Info "role '$dbUser' exists, aligning its password with .env"
            } else {
                $roleSql = "CREATE ROLE $dbUser WITH LOGIN PASSWORD '$dbPassword';"
                Write-Info "creating role '$dbUser'"
            }
            $roleResult = Invoke-Psql $psql $baseArgs $roleSql
            if ($roleResult.ExitCode -ne 0) { throw "Could not create or update the role '$dbUser'." }

            foreach ($name in @($devDatabase, $testDatabase)) {
                $exists = Invoke-Psql $psql ($baseArgs + @("-c", "SELECT 1 FROM pg_database WHERE datname='$name'"))
                if ($exists.Output -eq "1") { Write-Ok "database '$name' already exists and was left unchanged"; continue }
                $create = Invoke-Psql $psql ($baseArgs + @("-c", "CREATE DATABASE $name OWNER $dbUser TEMPLATE template0 ENCODING 'UTF8'"))
                if ($create.ExitCode -ne 0) { $create = Invoke-Psql $psql ($baseArgs + @("-c", "CREATE DATABASE $name OWNER $dbUser")) }
                if ($create.ExitCode -ne 0) { throw "Could not create database '$name'." }
                Write-Ok "created database '$name'"
            }
        } finally {
            Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
        }
    }
}

Write-Step "Backend (Python virtual environment)"
$venvPython = Get-VenvPython $backend
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $python.Path @($python.LauncherArgs) -m venv (Join-Path $backend ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment." }
    Write-Ok "created backend\.venv"
} else {
    Write-Ok "backend\.venv already exists"
}
Invoke-Native $venvPython @("-m", "pip", "install", "--upgrade", "pip") $backend
Invoke-Native $venvPython @("-m", "pip", "install", "-e", ".[dev]") $backend
Write-Ok "backend dependencies installed"

Write-Step "Database migrations"
Invoke-Native $venvPython @("-m", "alembic", "upgrade", "head") $backend
Write-Ok "the development database is at the latest migration"

if (-not $SkipFrontend) {
    Write-Step "Frontend (npm)"
    Invoke-Native "npm" @("install", "--no-audit", "--no-fund") $frontend
    Write-Ok "frontend dependencies installed"
    if (Test-Path -LiteralPath (Join-Path $frontend "package-lock.json")) {
        Write-Info "Commit frontend\package-lock.json so installs are reproducible."
    }
}

if ($WithWorker) {
    Write-Step "N-ATLAS inference worker environment"
    $worker = Join-Path (Join-Path $RepoPath "inference") "natlas-worker"
    $workerPython = Get-VenvPython $worker
    if (-not (Test-Path -LiteralPath $workerPython)) {
        & $python.Path @($python.LauncherArgs) -m venv (Join-Path $worker ".venv")
        if ($LASTEXITCODE -ne 0) { throw "Could not create the worker virtual environment." }
    }
    Invoke-Native $workerPython @("-m", "pip", "install", "--upgrade", "pip") $worker
    Invoke-Native $workerPython @("-m", "pip", "install", "-r", "requirements-dev.txt") $worker
    [void](Initialize-WorkerEnv $worker)
    Write-Ok "worker API dependencies installed and worker .env created with a generated token"
    Write-Warn2 "The model libraries are NOT installed yet. This is deliberate, because PyTorch depends on your hardware."
    Write-Info "1. Pick your PyTorch command at https://pytorch.org/get-started/locally/ and run it with the worker environment:"
    Write-Info "   inference\natlas-worker\.venv\Scripts\python.exe -m pip install <torch command from that page>"
    Write-Info "2. Then install the model libraries:"
    Write-Info "   inference\natlas-worker\.venv\Scripts\python.exe -m pip install -r inference\natlas-worker\requirements-model.txt"
    Write-Info "3. Accept the model terms on Hugging Face and set HF_TOKEN in inference\natlas-worker\.env"
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "  Start the app:   .\scripts\dev.ps1"
Write-Host "  Verify it works: .\scripts\smoke-day1.ps1   (after the app is running)"
Write-Host "  Quality gates:   .\scripts\check.ps1"

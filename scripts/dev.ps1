<#
.SYNOPSIS
  Starts the backend (port 8000) and the frontend (port 5173) in separate PowerShell windows.

.EXAMPLE
  .\scripts\dev.ps1
  .\scripts\dev.ps1 -WithWorker     # also starts the N-ATLAS inference worker on port 8081
#>
[CmdletBinding()]
param(
    [string]$RepoPath = (Split-Path -Parent $PSScriptRoot),
    [switch]$WithWorker,
    [int]$BackendPort = 8000,
    [int]$WorkerPort = 8081
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")

$RepoPath = (Resolve-Path -LiteralPath $RepoPath).Path
$backend = Join-Path $RepoPath "backend"
$frontend = Join-Path $RepoPath "frontend"
$worker = Join-Path (Join-Path $RepoPath "inference") "natlas-worker"

if (-not (Test-Path -LiteralPath (Join-Path $RepoPath ".env"))) { throw ".env was not found. Run .\scripts\setup-local.ps1 first." }
$venvPython = Get-VenvPython $backend
if (-not (Test-Path -LiteralPath $venvPython)) { throw "backend\.venv was not found. Run .\scripts\setup-local.ps1 first." }
if (-not (Test-Path -LiteralPath (Join-Path $frontend "node_modules"))) { throw "frontend\node_modules was not found. Run .\scripts\setup-local.ps1 first." }

if (-not (Test-TcpPort "localhost" 6379)) { Write-Warn2 "Redis is not reachable on localhost:6379. The backend will report it as unavailable." }

Write-Step "Starting the backend on http://127.0.0.1:$BackendPort"
$backendCommand = "& '$venvPython' -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port $BackendPort --reload"
Start-Process -FilePath "powershell.exe" -WorkingDirectory $backend -ArgumentList @("-NoExit", "-Command", $backendCommand)

Write-Step "Starting the frontend on http://localhost:5173"
Start-Process -FilePath "powershell.exe" -WorkingDirectory $frontend -ArgumentList @("-NoExit", "-Command", "npm run dev")

if ($WithWorker) {
    $workerPython = Get-VenvPython $worker
    if (-not (Test-Path -LiteralPath $workerPython)) { throw "The worker environment was not found. Run .\scripts\setup-local.ps1 -WithWorker first." }
    Write-Step "Starting the inference worker on http://127.0.0.1:$WorkerPort"
    $workerCommand = "& '$workerPython' -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port $WorkerPort"
    Start-Process -FilePath "powershell.exe" -WorkingDirectory $worker -ArgumentList @("-NoExit", "-Command", $workerCommand)
    Write-Info "The model loads in the background and can take several minutes."
}

Write-Host ""
Write-Host "Open http://localhost:5173 in your browser." -ForegroundColor Green
Write-Host "API documentation (development only): http://127.0.0.1:$BackendPort/api/docs"
Write-Host "Readiness: http://127.0.0.1:$BackendPort/api/v1/health/ready"
Write-Host "Close the new windows to stop the servers."

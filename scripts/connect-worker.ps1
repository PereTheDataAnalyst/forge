<#
.SYNOPSIS
  Points the backend at the N-ATLAS inference worker by writing NATLAS_INFERENCE_URL and
  NATLAS_INFERENCE_TOKEN into .env. The token is shared with the worker's own .env.

.EXAMPLE
  .\scripts\connect-worker.ps1 -Url http://127.0.0.1:8081
#>
[CmdletBinding()]
param(
    [string]$RepoPath = (Split-Path -Parent $PSScriptRoot),
    [string]$Url = "http://127.0.0.1:8081"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")

$RepoPath = (Resolve-Path -LiteralPath $RepoPath).Path
$envFile = Join-Path $RepoPath ".env"
$worker = Join-Path (Join-Path $RepoPath "inference") "natlas-worker"
if (-not (Test-Path -LiteralPath $envFile)) { throw ".env was not found. Run .\scripts\setup-local.ps1 first." }
if ($Url -notmatch '^https?://[^\s/]+(/\S*)?$') { throw "The URL must look like http://127.0.0.1:8081" }
$Url = $Url.TrimEnd('/')

Write-Step "Connecting the gateway to the inference worker"
$token = Initialize-WorkerEnv $worker
Set-EnvValue $envFile "NATLAS_INFERENCE_URL" $Url
Set-EnvValue $envFile "NATLAS_INFERENCE_TOKEN" $token
Write-Ok "NATLAS_INFERENCE_URL set to $Url"
Write-Ok "NATLAS_INFERENCE_TOKEN set (same value as the worker's NATLAS_WORKER_TOKEN, not shown)"
Write-Info "Restart the backend so it reads the new settings."
Write-Info "Then check: Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/ready"

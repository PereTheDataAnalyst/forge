<#
.SYNOPSIS
  Walks through the environment variables that the deployment platform's dashboard needs for N-ATLAS Forge.
  Secrets are generated here, copied to the clipboard one at a time and never written to disk.

.DESCRIPTION
  1. Prints the plain settings to add in the project's Environment tab.
  2. For each secret, copies a fresh value to the clipboard. Paste it into the Environment tab,
     then press Enter for the next one. The values are not printed on screen.
  3. Copies the inference token from your local .env (written by connect-worker.ps1) so the
     production gateway and the GPU worker keep sharing one token.

  Keep SECRET_KEY and API_KEY_PEPPER in a password manager. Changing API_KEY_PEPPER later
  invalidates every API key that was issued.

.EXAMPLE
  .\scripts\prod-env.ps1
  .\scripts\prod-env.ps1 -PublicUrl https://forge.example.com
#>
[CmdletBinding()]
param(
    [string]$RepoPath = (Split-Path -Parent $PSScriptRoot),
    [string]$PublicUrl = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")

$RepoPath = (Resolve-Path -LiteralPath $RepoPath).Path
$envFile = Join-Path $RepoPath ".env"

function Copy-AndWait([string]$Name, [string]$Value) {
    Set-Clipboard -Value $Value
    Write-Ok "$Name is on the clipboard ($($Value.Length) characters). Paste it into the Environment tab."
    [void](Read-Host "         Press Enter when it is pasted")
}

Write-Step "1. Plain settings (type these into the Environment tab)"
$publicValue = "https://<the address the platform shows after the first deploy>"
if ($PublicUrl -ne "") {
    if ($PublicUrl -notmatch '^https://[^\s/]+$') { throw "PublicUrl must look like https://forge.example.com (https, no path)." }
    $publicValue = $PublicUrl.TrimEnd('/')
}
$plain = @(
    @("APP_ENV", "production"),
    @("LOG_FORMAT", "json"),
    @("COOKIE_SECURE", "true"),
    @("ENABLE_API_DOCS", "false"),
    @("TRUSTED_PROXY_COUNT", "1"),
    @("REGISTRATION_ENABLED", "true"),
    @("PUBLIC_BASE_URL", $publicValue)
)
foreach ($pair in $plain) { Write-Info ("{0,-24} {1}" -f $pair[0], $pair[1]) }
Write-Info ""
Write-Info "DATABASE_URL and REDIS_URL come from the Databases and Redis pages of the dashboard."
Write-Info "NATLAS_INFERENCE_URL is the current worker address (see docs\model-hosting.md)."
[void](Read-Host "         Press Enter to continue to the secrets")

Write-Step "2. Secrets generated now (one at a time)"
Copy-AndWait "SECRET_KEY" (New-UrlSafeSecret 48)
Copy-AndWait "API_KEY_PEPPER" (New-UrlSafeSecret 48)

Write-Step "3. Inference token (read from your local .env)"
$token = Get-EnvValue $envFile "NATLAS_INFERENCE_TOKEN"
if ([string]::IsNullOrWhiteSpace($token)) {
    Write-Warn2 "NATLAS_INFERENCE_TOKEN is not in $envFile."
    Write-Info "Run .\scripts\connect-worker.ps1 -Url <worker address> first, or copy the token from the"
    Write-Info "Kaggle secret named NATLAS_WORKER_TOKEN. Both sides must hold the same value."
} else {
    Copy-AndWait "NATLAS_INFERENCE_TOKEN" $token
}

Write-Host ""
Write-Ok "Done. Clear the clipboard now (copy any text) and save the two secrets in a password manager."
Write-Info "Next: deploy, read the address the platform assigns, set PUBLIC_BASE_URL to it, and deploy again."

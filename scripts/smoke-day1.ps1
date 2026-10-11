<#
.SYNOPSIS
  Exercises the running application end to end over HTTP and reports PASS or FAIL per step.

.DESCRIPTION
  Registers a throwaway account, then verifies CSRF protection, project creation, API key
  creation (shown once, never listed), gateway authentication, the N-ATLAS chat path, the
  dashboard, key revocation, project archiving and logout.
  N-ATLAS inference is reported honestly: VERIFIED only when the real model returned text,
  otherwise NOT CONNECTED with the structured error the gateway produced. Nothing is faked.

.EXAMPLE
  .\scripts\smoke-day1.ps1
  .\scripts\smoke-day1.ps1 -BaseUrl http://127.0.0.1:8000
#>
[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$Origin = "http://localhost:5173"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lib\common.ps1")
Add-Type -AssemblyName System.Net.Http

$BaseUrl = $BaseUrl.TrimEnd('/')
$handler = New-Object System.Net.Http.HttpClientHandler
$handler.UseCookies = $true
$handler.CookieContainer = New-Object System.Net.CookieContainer
$client = New-Object System.Net.Http.HttpClient($handler)
$client.Timeout = [TimeSpan]::FromSeconds(300)
$baseUri = New-Object System.Uri($BaseUrl)
$script:failures = 0

function Invoke-Api {
    param(
        [string]$Method,
        [string]$Path,
        [string]$JsonBody = "",
        [hashtable]$Headers = @{},
        [bool]$Csrf = $true
    )
    $httpMethod = New-Object System.Net.Http.HttpMethod($Method)
    $request = New-Object System.Net.Http.HttpRequestMessage($httpMethod, ($BaseUrl + $Path))
    [void]$request.Headers.TryAddWithoutValidation("Accept", "application/json")
    [void]$request.Headers.TryAddWithoutValidation("Origin", $Origin)
    foreach ($name in $Headers.Keys) {
        [void]$request.Headers.TryAddWithoutValidation($name, [string]$Headers[$name])
    }
    if ($Method -ne "GET" -and $Csrf) {
        $cookie = $handler.CookieContainer.GetCookies($baseUri)["forge_csrf"]
        if ($null -ne $cookie) { [void]$request.Headers.TryAddWithoutValidation("X-CSRF-Token", $cookie.Value) }
    }
    if ($JsonBody -ne "") {
        $request.Content = New-Object System.Net.Http.StringContent($JsonBody, [System.Text.Encoding]::UTF8, "application/json")
    }
    $response = $client.SendAsync($request).GetAwaiter().GetResult()
    $text = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    $json = $null
    if ($text) {
        try { $json = $text | ConvertFrom-Json } catch { $json = $null }
    }
    $requestId = $null
    $values = $null
    if ($response.Headers.TryGetValues("X-Request-ID", [ref]$values)) { $requestId = @($values)[0] }
    return @{ Status = [int]$response.StatusCode; Json = $json; Text = $text; RequestId = $requestId }
}

function Assert-Step([string]$Name, [bool]$Condition, [string]$Detail = "") {
    if ($Condition) {
        Write-Ok $Name
    } else {
        Write-Fail "$Name $Detail"
        $script:failures++
    }
}

function Get-ErrorCode($Result) {
    if ($null -ne $Result.Json -and $null -ne $Result.Json.error) { return [string]$Result.Json.error.code }
    return ""
}

function Exit-WithSummary([bool]$InferenceVerified) {
    Write-Host ""
    if ($InferenceVerified) {
        Write-Host "N-ATLAS inference: VERIFIED (the real model returned generated text)." -ForegroundColor Green
    } else {
        Write-Host "N-ATLAS inference: NOT CONNECTED. Connect the worker (see docs\n-atlas-integration.md), then run this script again." -ForegroundColor Yellow
    }
    if ($script:failures -gt 0) {
        Write-Host "$($script:failures) step(s) FAILED." -ForegroundColor Red
        exit 1
    }
    Write-Host "All required steps passed." -ForegroundColor Green
    exit 0
}

Write-Host "N-ATLAS Forge live smoke test against $BaseUrl"

Write-Step "1. Health"
try {
    $live = Invoke-Api -Method GET -Path "/api/v1/health/live"
} catch {
    Write-Fail "Could not reach $BaseUrl. Start the backend with .\scripts\dev.ps1 and try again."
    exit 1
}
Assert-Step "liveness endpoint answers 200" ($live.Status -eq 200) "(status $($live.Status))"
$ready = Invoke-Api -Method GET -Path "/api/v1/health/ready"
Assert-Step "PostgreSQL is reachable" ($ready.Json.checks.database.status -eq "ok")
Assert-Step "Redis is reachable" ($ready.Json.checks.redis.status -eq "ok")
Write-Info "readiness: $($ready.Json.status), N-ATLAS provider: $($ready.Json.checks.provider.status)"
if ($script:failures -gt 0) { Exit-WithSummary $false }

Write-Step "2. Account and session"
$email = "smoke-" + (New-AlphaNumericSecret 10).ToLower() + "@example.com"
$password = "Smoke-" + (New-AlphaNumericSecret 16) + "-9"
$registerBody = @{ email = $email; password = $password; display_name = "Smoke Test" } | ConvertTo-Json -Compress
$register = Invoke-Api -Method POST -Path "/api/v1/auth/register" -JsonBody $registerBody
Assert-Step "registration returns 201" ($register.Status -eq 201) "(status $($register.Status), code $(Get-ErrorCode $register))"
if ($register.Status -ne 201) { Exit-WithSummary $false }
$cookies = $handler.CookieContainer.GetCookies($baseUri)
Assert-Step "session cookie is HTTP-only" ($null -ne $cookies["forge_session"] -and $cookies["forge_session"].HttpOnly)
Assert-Step "CSRF cookie is set" ($null -ne $cookies["forge_csrf"])
$me = Invoke-Api -Method GET -Path "/api/v1/auth/me"
Assert-Step "the session identifies the new user" ($me.Status -eq 200 -and $me.Json.email -eq $email)

Write-Step "3. CSRF protection"
$forged = Invoke-Api -Method POST -Path "/api/v1/projects" -JsonBody '{"name":"No CSRF token"}' -Csrf $false
Assert-Step "a state-changing request without a CSRF token is rejected" ($forged.Status -eq 403 -and (Get-ErrorCode $forged) -eq "csrf_validation_failed") "(status $($forged.Status))"

Write-Step "4. Project and API key"
$project = Invoke-Api -Method POST -Path "/api/v1/projects" -JsonBody '{"name":"Smoke test project","description":"Created by smoke-day1.ps1"}'
Assert-Step "project creation returns 201" ($project.Status -eq 201) "(status $($project.Status), code $(Get-ErrorCode $project))"
if ($project.Status -ne 201) { Exit-WithSummary $false }
$projectId = [string]$project.Json.id
$key = Invoke-Api -Method POST -Path "/api/v1/projects/$projectId/api-keys" -JsonBody '{"name":"Smoke key"}'
Assert-Step "API key creation returns 201 with a full key" ($key.Status -eq 201 -and ([string]$key.Json.key) -like "nfk_*") "(status $($key.Status))"
if ($key.Status -ne 201) { Exit-WithSummary $false }
$apiKey = [string]$key.Json.key
$keyId = [string]$key.Json.id
$bearer = @{ Authorization = "Bearer $apiKey" }
$list = Invoke-Api -Method GET -Path "/api/v1/projects/$projectId/api-keys"
Assert-Step "the key listing never contains the full key" ($list.Status -eq 200 -and $list.Text.IndexOf($apiKey, [System.StringComparison]::Ordinal) -lt 0)

Write-Step "5. Gateway authentication"
$noKey = Invoke-Api -Method GET -Path "/api/v1/models"
Assert-Step "no API key is rejected, and browser cookies do not count as one" ($noKey.Status -eq 401 -and (Get-ErrorCode $noKey) -eq "missing_api_key")
$models = Invoke-Api -Method GET -Path "/api/v1/models" -Headers $bearer
Assert-Step "GET /api/v1/models with the key returns 200" ($models.Status -eq 200) "(status $($models.Status))"
if ($models.Status -eq 200) { Write-Info "model $($models.Json.data[0].id) status: $($models.Json.data[0].status)" }
$invalid = Invoke-Api -Method POST -Path "/api/v1/chat" -JsonBody '{"messages":[]}' -Headers $bearer
Assert-Step "invalid input returns 422 with a structured error" ($invalid.Status -eq 422 -and (Get-ErrorCode $invalid) -eq "validation_error")

Write-Step "6. N-ATLAS inference path"
$chatBody = '{"messages":[{"role":"user","content":"Say hello in one short sentence."}],"max_tokens":64,"language":"en"}'
$chat = Invoke-Api -Method POST -Path "/api/v1/chat" -JsonBody $chatBody -Headers $bearer
$inferenceVerified = $false
if ($chat.Status -eq 200) {
    $reply = [string]$chat.Json.message.content
    Assert-Step "the model returned generated text" ($reply.Length -gt 0)
    Assert-Step "usage, latency and request ID are reported" ($chat.Json.usage.total_tokens -gt 0 -and $null -ne $chat.Json.request_id -and $chat.Json.latency_ms -ge 0)
    $inferenceVerified = ($reply.Length -gt 0)
    $shown = $reply
    if ($shown.Length -gt 300) { $shown = $shown.Substring(0, 300) }
    Write-Info "reply: $shown"
    Write-Info "tokens: $($chat.Json.usage.total_tokens), latency: $($chat.Json.latency_ms) ms, request: $($chat.Json.request_id)"
} else {
    $code = Get-ErrorCode $chat
    $expected = @("provider_not_configured", "provider_unavailable", "provider_loading", "provider_busy", "provider_timeout")
    if ($expected -contains $code) {
        Write-Warn2 "inference is not available yet ($code). This is expected until the worker is connected."
        Assert-Step "the gateway reports it as a structured error with a request ID" ($null -ne $chat.RequestId -and $null -ne $chat.Json.error.request_id)
    } else {
        Assert-Step "chat request" $false "(status $($chat.Status), code $code)"
    }
}

Write-Step "7. Dashboard"
$dashboard = Invoke-Api -Method GET -Path "/api/v1/dashboard/summary"
Assert-Step "the dashboard counts the request" ($dashboard.Status -eq 200 -and $dashboard.Json.totals.requests -ge 1)
if ($dashboard.Status -eq 200) {
    Write-Info "requests: $($dashboard.Json.totals.requests), successful: $($dashboard.Json.totals.successful), failed: $($dashboard.Json.totals.failed)"
}

Write-Step "8. Cleanup and logout"
$revoke = Invoke-Api -Method POST -Path "/api/v1/projects/$projectId/api-keys/$keyId/revoke"
Assert-Step "revoking the key returns 200" ($revoke.Status -eq 200 -and $revoke.Json.status -eq "revoked")
$after = Invoke-Api -Method GET -Path "/api/v1/models" -Headers $bearer
Assert-Step "a revoked key stops working immediately" ($after.Status -eq 401 -and (Get-ErrorCode $after) -eq "api_key_revoked")
$archive = Invoke-Api -Method DELETE -Path "/api/v1/projects/$projectId"
Assert-Step "archiving the project returns 204" ($archive.Status -eq 204)
$logout = Invoke-Api -Method POST -Path "/api/v1/auth/logout"
Assert-Step "logout returns 204" ($logout.Status -eq 204)
$gone = Invoke-Api -Method GET -Path "/api/v1/auth/me"
Assert-Step "the session no longer works after logout" ($gone.Status -eq 401)

Exit-WithSummary $inferenceVerified

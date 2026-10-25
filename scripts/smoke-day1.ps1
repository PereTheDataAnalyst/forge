<#
.SYNOPSIS
  Exercises the running application end to end over HTTP and reports PASS or FAIL per step.

.DESCRIPTION
  Registers a throwaway account, then verifies CSRF protection, project creation, API key
  creation (shown once, never listed), gateway authentication, the N-ATLAS chat path, the
  Playground, the Evaluation Lab, request logs and charts, Voice, MCP, the dashboard, key
  revocation, project archiving and logout.
  N-ATLAS inference is reported honestly: VERIFIED only when the real model returned text,
  otherwise NOT CONNECTED with the structured error the gateway produced. Speech recognition and
  the MCP server are reported the same way. Nothing is faked.

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
        [bool]$Csrf = $true,
        [byte[]]$Bytes = $null,
        [string]$ContentType = "",
        [bool]$SendOrigin = $true
    )
    $httpMethod = New-Object System.Net.Http.HttpMethod($Method)
    $request = New-Object System.Net.Http.HttpRequestMessage($httpMethod, ($BaseUrl + $Path))
    [void]$request.Headers.TryAddWithoutValidation("Accept", "application/json")
    if ($SendOrigin) { [void]$request.Headers.TryAddWithoutValidation("Origin", $Origin) }
    foreach ($name in $Headers.Keys) {
        if ($name -eq "Accept") { [void]$request.Headers.Remove("Accept") }
        [void]$request.Headers.TryAddWithoutValidation($name, [string]$Headers[$name])
    }
    if ($Method -ne "GET" -and $Csrf) {
        $cookie = $handler.CookieContainer.GetCookies($baseUri)["forge_csrf"]
        if ($null -ne $cookie) { [void]$request.Headers.TryAddWithoutValidation("X-CSRF-Token", $cookie.Value) }
    }
    if ($JsonBody -ne "") {
        $request.Content = New-Object System.Net.Http.StringContent($JsonBody, [System.Text.Encoding]::UTF8, "application/json")
    }
    if ($null -ne $Bytes) {
        $request.Content = New-Object System.Net.Http.ByteArrayContent(, $Bytes)
        if ($ContentType -ne "") { [void]$request.Content.Headers.TryAddWithoutValidation("Content-Type", $ContentType) }
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

function New-SilentWav([int]$Seconds = 1) {
    $rate = 16000
    $dataBytes = $rate * 2 * $Seconds
    $stream = New-Object System.IO.MemoryStream
    $writer = New-Object System.IO.BinaryWriter($stream)
    $writer.Write([System.Text.Encoding]::ASCII.GetBytes("RIFF"))
    $writer.Write([int](36 + $dataBytes))
    $writer.Write([System.Text.Encoding]::ASCII.GetBytes("WAVEfmt "))
    $writer.Write([int]16)
    $writer.Write([int16]1)
    $writer.Write([int16]1)
    $writer.Write([int]$rate)
    $writer.Write([int]($rate * 2))
    $writer.Write([int16]2)
    $writer.Write([int16]16)
    $writer.Write([System.Text.Encoding]::ASCII.GetBytes("data"))
    $writer.Write([int]$dataBytes)
    $writer.Write((New-Object byte[] $dataBytes))
    $writer.Flush()
    return , $stream.ToArray()
}

function New-McpBody([string]$Method, [hashtable]$Params = @{}) {
    $meta = @{
        "io.modelcontextprotocol/protocolVersion" = "2026-07-28"
        "io.modelcontextprotocol/clientCapabilities" = @{}
        "io.modelcontextprotocol/clientInfo" = @{ name = "forge-smoke"; version = "1" }
    }
    $merged = @{}
    foreach ($name in $Params.Keys) { $merged[$name] = $Params[$name] }
    $merged["_meta"] = $meta
    return (@{ jsonrpc = "2.0"; id = 1; method = $Method; params = $merged } | ConvertTo-Json -Depth 8 -Compress)
}

function Get-McpHeaders([string]$Token, [string]$Method, [string]$Name = "") {
    $headers = @{
        Accept = "application/json, text/event-stream"
        "MCP-Protocol-Version" = "2026-07-28"
        "Mcp-Method" = $Method
    }
    if ($Token -ne "") { $headers["Authorization"] = "Bearer $Token" }
    if ($Name -ne "") { $headers["Mcp-Name"] = $Name }
    return $headers
}

$script:voiceOutcome = $null
$script:mcpOutcome = $null

function Exit-WithSummary([bool]$InferenceVerified) {
    Write-Host ""
    if ($InferenceVerified) {
        Write-Host "N-ATLAS inference: VERIFIED (the real model returned generated text)." -ForegroundColor Green
    } else {
        Write-Host "N-ATLAS inference: NOT CONNECTED. Connect the worker (see docs\n-atlas-integration.md), then run this script again." -ForegroundColor Yellow
    }
    if ($script:voiceOutcome -eq "verified") {
        Write-Host "Speech recognition: VERIFIED (a speech worker answered. This script does not measure accuracy)." -ForegroundColor Green
    } elseif ($script:voiceOutcome -eq "not-configured") {
        Write-Host "Speech recognition: NOT CONNECTED. No speech worker is configured, as expected until one is deployed (see docs\voice.md)." -ForegroundColor Yellow
    } elseif ($script:voiceOutcome -eq "unavailable") {
        Write-Host "Speech recognition: configured but did not answer. See docs\voice.md." -ForegroundColor Yellow
    }
    if ($script:mcpOutcome -eq "verified") {
        Write-Host "MCP server: VERIFIED (a client listed tools and called one through /mcp, then lost access when revoked)." -ForegroundColor Green
    } elseif ($script:mcpOutcome -eq "not-running") {
        Write-Host "MCP server: NOT RUNNING on this deployment. See docs\mcp.md." -ForegroundColor Yellow
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

Write-Step "6b. Playground (browser session, no API key)"
$pgBase = "/api/v1/projects/$projectId/playground"
$providerCodes = @("provider_not_configured", "provider_unavailable", "provider_loading", "provider_busy", "provider_timeout")
$pgModels = Invoke-Api -Method GET -Path "$pgBase/models"
Assert-Step "the playground lists the text model" ($pgModels.Status -eq 200 -and @($pgModels.Json.data).Count -ge 1 -and $pgModels.Json.data[0].kind -eq "llm") "(status $($pgModels.Status))"
$pgForged = Invoke-Api -Method POST -Path "$pgBase/chat" -JsonBody $chatBody -Csrf $false
Assert-Step "a playground request without a CSRF token is rejected" ($pgForged.Status -eq 403 -and (Get-ErrorCode $pgForged) -eq "csrf_validation_failed") "(status $($pgForged.Status))"
$pgChat = Invoke-Api -Method POST -Path "$pgBase/chat" -JsonBody $chatBody
if ($pgChat.Status -eq 200) {
    $pgReply = [string]$pgChat.Json.message.content
    Assert-Step "the playground returns generated text" ($pgReply.Length -gt 0)
    Assert-Step "the playground reports usage, latency and a request ID" ($pgChat.Json.usage.total_tokens -gt 0 -and $null -ne $pgChat.Json.request_id -and $pgChat.Json.latency_ms -ge 0)
} else {
    $pgCode = Get-ErrorCode $pgChat
    if ($providerCodes -contains $pgCode) {
        Write-Warn2 "the playground reached the gateway, but inference is not available ($pgCode)."
        Assert-Step "the playground reports it as a structured error with a request ID" ($null -ne $pgChat.RequestId -and $null -ne $pgChat.Json.error.request_id)
    } else {
        Assert-Step "playground chat request" $false "(status $($pgChat.Status), code $pgCode)"
    }
}

Write-Step "6c. Evaluation Lab (browser session, no API key)"
$evStarters = Invoke-Api -Method GET -Path "/api/v1/evaluations/starters"
Assert-Step "the starter datasets are listed" ($evStarters.Status -eq 200 -and @($evStarters.Json.data).Count -ge 1) "(status $($evStarters.Status))"
$evBase = "/api/v1/projects/$projectId"
$evForged = Invoke-Api -Method POST -Path "$evBase/evaluations" -JsonBody '{"starter_id":"starter-general-checks"}' -Csrf $false
Assert-Step "adding a dataset without a CSRF token is rejected" ($evForged.Status -eq 403 -and (Get-ErrorCode $evForged) -eq "csrf_validation_failed") "(status $($evForged.Status))"
$evCreate = Invoke-Api -Method POST -Path "$evBase/evaluations" -JsonBody '{"starter_id":"starter-general-checks"}'
Assert-Step "a starter dataset is added" ($evCreate.Status -eq 201 -and $evCreate.Json.case_count -ge 1) "(status $($evCreate.Status))"
$evId = [string]$evCreate.Json.id
$evBad = Invoke-Api -Method POST -Path "$evBase/evaluations" -JsonBody '{"dataset":{"name":"Broken","cases":[{"id":"c1","category":"general","checks":[]}]}}'
Assert-Step "a broken dataset is refused with a message per problem" ($evBad.Status -eq 422 -and (Get-ErrorCode $evBad) -eq "invalid_dataset" -and @($evBad.Json.error.details).Count -ge 1) "(status $($evBad.Status))"
$evRunStart = Invoke-Api -Method POST -Path "$evBase/evaluation-runs" -JsonBody ('{"evaluation_id":"' + $evId + '"}')
if ($pgChat.Status -eq 200) {
    Assert-Step "an evaluation run starts" ($evRunStart.Status -eq 202 -and ([string]$evRunStart.Json.run_id).Length -eq 32) "(status $($evRunStart.Status))"
    $evRunId = [string]$evRunStart.Json.run_id
    $evDeadline = (Get-Date).AddSeconds(240)
    $evBody = $null
    while ((Get-Date) -lt $evDeadline) {
        $evPoll = Invoke-Api -Method GET -Path "$evBase/evaluation-runs/$evRunId"
        if ($evPoll.Status -ne 200) { break }
        $evBody = $evPoll.Json
        if ($evBody.state -ne "running" -and $null -ne $evBody.handle -and $null -ne $evBody.handle.persisted) { break }
        Start-Sleep -Seconds 2
    }
    Assert-Step "the run completes against the real model" ($null -ne $evBody -and $evBody.state -eq "completed") "(state $($evBody.state))"
    Assert-Step "the run is saved with a result for every case" ($evBody.handle.persisted -eq $true -and @($evBody.run.results).Count -eq $evCreate.Json.case_count)
    $evSummary = $evBody.run.summary
    Write-Info "pass rate $($evSummary.pass_rate), passed $($evSummary.counts.pass), failed $($evSummary.counts.fail), needs review $($evSummary.counts.review), errors $($evSummary.counts.error)"
    $evExport = Invoke-Api -Method GET -Path "$evBase/evaluation-runs/$evRunId/export?format=json"
    Assert-Step "the run exports as JSON" ($evExport.Status -eq 200) "(status $($evExport.Status))"
} else {
    Write-Warn2 "inference is not available, so the evaluation run itself was not exercised."
    Assert-Step "a run is refused with a structured error while the model is away" ($evRunStart.Status -eq 503 -and (Get-ErrorCode $evRunStart) -eq "provider_unavailable" -and $null -ne $evRunStart.Json.error.request_id) "(status $($evRunStart.Status))"
}
$evArchive = Invoke-Api -Method DELETE -Path "$evBase/evaluations/$evId"
Assert-Step "the dataset can be archived" ($evArchive.Status -eq 204) "(status $($evArchive.Status))"

Write-Step "6d. Request logs and charts (browser session, no API key)"
$logBase = "/api/v1/projects/$projectId/requests"
$logList = Invoke-Api -Method GET -Path "${logBase}?range=24h&limit=5"
Assert-Step "the request log lists this project's requests" ($logList.Status -eq 200 -and @($logList.Json.data).Count -ge 1) "(status $($logList.Status))"
if ($logList.Status -eq 200 -and @($logList.Json.data).Count -ge 1) {
    $logRow = $logList.Json.data[0]
    Assert-Step "a log row has a request ID, a source and an outcome" (([string]$logRow.request_id).Length -gt 0 -and ([string]$logRow.source).Length -gt 0 -and ([string]$logRow.outcome).Length -gt 0)
    $logFound = Invoke-Api -Method GET -Path "${logBase}?request_id=$([string]$logRow.request_id)"
    Assert-Step "a request can be found by its ID" ($logFound.Status -eq 200 -and @($logFound.Json.data).Count -ge 1)
}
$logMetrics = Invoke-Api -Method GET -Path "${logBase}/metrics?range=24h"
Assert-Step "metrics report totals and a zero-filled time series" ($logMetrics.Status -eq 200 -and $logMetrics.Json.totals.requests -ge 1 -and @($logMetrics.Json.series).Count -ge 24) "(status $($logMetrics.Status))"
if ($logMetrics.Status -eq 200) {
    Write-Info "last 24 hours: $($logMetrics.Json.totals.requests) requests, $($logMetrics.Json.totals.failed) failed, p95 latency of successes: $($logMetrics.Json.totals.latency.p95_ms) ms"
}
$logBadRange = Invoke-Api -Method GET -Path "${logBase}?range=2d"
Assert-Step "an unknown range is refused" ($logBadRange.Status -eq 422) "(status $($logBadRange.Status))"
$logFacets = Invoke-Api -Method GET -Path "${logBase}/facets?range=24h"
Assert-Step "the filter lists load" ($logFacets.Status -eq 200) "(status $($logFacets.Status))"

Write-Step "6e. Voice (browser session and API key)"
$vcStatus = Invoke-Api -Method GET -Path "/api/v1/voice/status"
Assert-Step "voice status lists the official model for each language" ($vcStatus.Status -eq 200 -and @($vcStatus.Json.languages).Count -eq 4 -and $vcStatus.Json.limits.max_seconds -ge 1) "(status $($vcStatus.Status))"
Write-Info "speech worker state: $($vcStatus.Json.state)"
$wav = New-SilentWav 1
$vcPath = "/api/v1/voice/transcriptions?project_id=$projectId&language=yo"
$vcForged = Invoke-Api -Method POST -Path $vcPath -Bytes $wav -ContentType "audio/wav" -Csrf $false
Assert-Step "an upload without a CSRF token is rejected" ($vcForged.Status -eq 403 -and (Get-ErrorCode $vcForged) -eq "csrf_validation_failed") "(status $($vcForged.Status))"
$vcForm = Invoke-Api -Method POST -Path $vcPath -Bytes $wav -ContentType "multipart/form-data; boundary=x"
Assert-Step "a form upload is refused with a clear message" ($vcForm.Status -eq 415 -and (Get-ErrorCode $vcForm) -eq "unsupported_media_type") "(status $($vcForm.Status))"
$vcPost = Invoke-Api -Method POST -Path $vcPath -Bytes $wav -ContentType "audio/wav"
if ($vcStatus.Json.state -eq "not_configured") {
    Assert-Step "with no speech worker the answer is an honest 503 with a request ID" ($vcPost.Status -eq 503 -and (Get-ErrorCode $vcPost) -eq "provider_not_configured" -and $null -ne $vcPost.Json.error.request_id) "(status $($vcPost.Status))"
    $script:voiceOutcome = "not-configured"
} elseif ($vcPost.Status -eq 200) {
    Assert-Step "the speech worker returned a transcript with usage details" ($null -ne $vcPost.Json.text -and ([string]$vcPost.Json.model) -like "NCAIR1/*" -and $vcPost.Json.audio.size_bytes -gt 0 -and $vcPost.Json.latency_ms -ge 0)
    Write-Info "model: $($vcPost.Json.model), latency: $($vcPost.Json.latency_ms) ms, request: $($vcPost.Json.request_id)"
    Write-Info "A second of silence only proves the path works. Read a real clip to judge accuracy."
    $script:voiceOutcome = "verified"
} else {
    $vcCode = Get-ErrorCode $vcPost
    if ($providerCodes -contains $vcCode) {
        Write-Warn2 "a speech worker is configured, but it did not answer ($vcCode)."
        Assert-Step "the failure is a structured error with a request ID" ($null -ne $vcPost.RequestId -and $null -ne $vcPost.Json.error.request_id)
        $script:voiceOutcome = "unavailable"
    } else {
        Assert-Step "voice transcription request" $false "(status $($vcPost.Status), code $vcCode)"
    }
}
$vcDenied = Invoke-Api -Method POST -Path "/api/v1/audio/transcriptions?language=yo" -Bytes $wav -ContentType "audio/wav" -Headers $bearer
Assert-Step "a key without the asr.transcribe scope is refused" ($vcDenied.Status -eq 403 -and (Get-ErrorCode $vcDenied) -eq "insufficient_scope") "(status $($vcDenied.Status))"
$vcKey = Invoke-Api -Method POST -Path "/api/v1/projects/$projectId/api-keys" -JsonBody '{"name":"Smoke voice key","scopes":["asr.transcribe"]}'
Assert-Step "a key with the asr.transcribe scope is created" ($vcKey.Status -eq 201 -and ([string]$vcKey.Json.key) -like "nfk_*") "(status $($vcKey.Status), code $(Get-ErrorCode $vcKey))"
if ($vcKey.Status -eq 201) {
    $vcKeyId = [string]$vcKey.Json.id
    $vcBearer = @{ Authorization = "Bearer $([string]$vcKey.Json.key)" }
    $vcApi = Invoke-Api -Method POST -Path "/api/v1/audio/transcriptions?language=yo" -Bytes $wav -ContentType "audio/wav" -Headers $vcBearer
    if ($vcStatus.Json.state -eq "not_configured") {
        Assert-Step "the developer API gives the same honest 503" ($vcApi.Status -eq 503 -and (Get-ErrorCode $vcApi) -eq "provider_not_configured") "(status $($vcApi.Status))"
    } elseif ($vcApi.Status -eq 200) {
        Assert-Step "the developer API returns a transcript and a request ID" ($null -ne $vcApi.Json.text -and $null -ne $vcApi.Json.request_id)
    } else {
        Assert-Step "the developer API reports a structured provider error" ($providerCodes -contains (Get-ErrorCode $vcApi)) "(status $($vcApi.Status), code $(Get-ErrorCode $vcApi))"
    }
    $vcLogs = Invoke-Api -Method GET -Path "${logBase}?source=voice&limit=10"
    Assert-Step "voice requests appear in the request log with source voice" ($vcLogs.Status -eq 200 -and @($vcLogs.Json.data).Count -ge 1) "(status $($vcLogs.Status))"
    $vcRecent = Invoke-Api -Method GET -Path "/api/v1/projects/$projectId/voice/requests"
    Assert-Step "recent voice requests list metadata only, never a transcript" ($vcRecent.Status -eq 200 -and $vcRecent.Text.IndexOf('"text"', [System.StringComparison]::Ordinal) -lt 0) "(status $($vcRecent.Status))"
    $vcRevoke = Invoke-Api -Method POST -Path "/api/v1/projects/$projectId/api-keys/$vcKeyId/revoke"
    Assert-Step "the voice key is revoked" ($vcRevoke.Status -eq 200)
}

Write-Step "6f. MCP (client management and the /mcp endpoint)"
$mcCatalog = Invoke-Api -Method GET -Path "/api/v1/mcp/catalog"
Assert-Step "the MCP catalog lists six kinds of access" ($mcCatalog.Status -eq 200 -and @($mcCatalog.Json.groups).Count -eq 6) "(status $($mcCatalog.Status))"
Write-Info "MCP server state: $($mcCatalog.Json.state)"
$mcBase = "/api/v1/projects/$projectId/mcp/clients"
$mcBadAccess = Invoke-Api -Method POST -Path $mcBase -JsonBody '{"name":"Bad","access":["admin"],"expires_in_days":30}'
Assert-Step "an unknown kind of access is refused" ($mcBadAccess.Status -eq 422 -and (Get-ErrorCode $mcBadAccess) -eq "invalid_access") "(status $($mcBadAccess.Status))"
$mcForged = Invoke-Api -Method POST -Path $mcBase -JsonBody '{"name":"No CSRF","access":["models:read"],"expires_in_days":30}' -Csrf $false
Assert-Step "creating a client without a CSRF token is rejected" ($mcForged.Status -eq 403 -and (Get-ErrorCode $mcForged) -eq "csrf_validation_failed") "(status $($mcForged.Status))"
$mcCreate = Invoke-Api -Method POST -Path $mcBase -JsonBody '{"name":"Smoke MCP client","access":["models:read","project:read","generate"],"expires_in_days":30}'
Assert-Step "an MCP client is created and its key is shown once" ($mcCreate.Status -eq 201 -and ([string]$mcCreate.Json.key) -like "nfk_*") "(status $($mcCreate.Status), code $(Get-ErrorCode $mcCreate))"
if ($mcCreate.Status -eq 201) {
    $mcKey = [string]$mcCreate.Json.key
    $mcClientId = [string]$mcCreate.Json.id
    $mcList = Invoke-Api -Method GET -Path $mcBase
    Assert-Step "the client listing never contains the key" ($mcList.Status -eq 200 -and $mcList.Text.IndexOf($mcKey, [System.StringComparison]::Ordinal) -lt 0)
    $mcRest = Invoke-Api -Method GET -Path "/api/v1/models" -Headers @{ Authorization = "Bearer $mcKey" }
    Assert-Step "an MCP key does not open the REST gateway" ($mcRest.Status -eq 403 -and (Get-ErrorCode $mcRest) -eq "insufficient_scope") "(status $($mcRest.Status))"
    $mcNoToken = Invoke-Api -Method POST -Path "/mcp" -JsonBody (New-McpBody "tools/list") -Headers (Get-McpHeaders "" "tools/list") -Csrf $false -SendOrigin $false
    if ($mcCatalog.Json.state -eq "ready") {
        Assert-Step "the MCP endpoint refuses a request without a token" ($mcNoToken.Status -eq 401) "(status $($mcNoToken.Status))"
        $mcTools = Invoke-Api -Method POST -Path "/mcp" -JsonBody (New-McpBody "tools/list") -Headers (Get-McpHeaders $mcKey "tools/list") -Csrf $false -SendOrigin $false
        Assert-Step "the MCP endpoint lists tools for the client" ($mcTools.Status -eq 200 -and $null -ne $mcTools.Json.result.tools) "(status $($mcTools.Status), $($mcTools.Text))"
        $toolNames = @($mcTools.Json.result.tools | ForEach-Object { [string]$_.name })
        Assert-Step "only the tools this client was given are listed" (($toolNames -contains "get_model_info") -and ($toolNames -contains "generate") -and (-not ($toolNames -contains "transcribe")) -and (-not ($toolNames -contains "evaluate"))) "($($toolNames -join ', '))"
        Assert-Step "the MCP response carries the platform request ID" ($null -ne $mcTools.RequestId)
        $mcInfo = Invoke-Api -Method POST -Path "/mcp" -JsonBody (New-McpBody "tools/call" @{ name = "get_model_info"; arguments = @{} }) -Headers (Get-McpHeaders $mcKey "tools/call" "get_model_info") -Csrf $false -SendOrigin $false
        Assert-Step "a client can call get_model_info" ($mcInfo.Status -eq 200 -and $mcInfo.Text -like "*N-ATLaS*") "(status $($mcInfo.Status))"
        $mcCalled = ($mcInfo.Status -eq 200 -and $mcInfo.Text -like "*N-ATLaS*")
        if ($inferenceVerified) {
            $mcGenerate = Invoke-Api -Method POST -Path "/mcp" -JsonBody (New-McpBody "tools/call" @{ name = "generate"; arguments = @{ prompt = "Say hello in one short sentence."; language = "en" } }) -Headers (Get-McpHeaders $mcKey "tools/call" "generate") -Csrf $false -SendOrigin $false
            Assert-Step "a client can generate text through MCP" ($mcGenerate.Status -eq 200 -and $null -ne $mcGenerate.Json -and $mcGenerate.Json.result.isError -ne $true) "(status $($mcGenerate.Status))"
            $mcLogs = Invoke-Api -Method GET -Path "${logBase}?source=mcp&limit=5"
            Assert-Step "the MCP call appears in the request log with source mcp" ($mcLogs.Status -eq 200 -and @($mcLogs.Json.data).Count -ge 1) "(status $($mcLogs.Status))"
        } else {
            Write-Info "inference is not available, so generating text through MCP was not exercised."
        }
        $mcRevoke = Invoke-Api -Method POST -Path "$mcBase/$mcClientId/revoke"
        Assert-Step "revoking the client returns 200" ($mcRevoke.Status -eq 200 -and $mcRevoke.Json.status -eq "revoked") "(status $($mcRevoke.Status))"
        $mcAfter = Invoke-Api -Method POST -Path "/mcp" -JsonBody (New-McpBody "tools/list") -Headers (Get-McpHeaders $mcKey "tools/list") -Csrf $false -SendOrigin $false
        Assert-Step "a revoked client loses MCP access at once" ($mcAfter.Status -eq 401) "(status $($mcAfter.Status))"
        if ($mcCalled) { $script:mcpOutcome = "verified" }
    } else {
        Write-Warn2 "the MCP server is not running on this deployment: $($mcCatalog.Json.message)"
        Assert-Step "the endpoint answers 503 with a clear code while the server is not running" ($mcNoToken.Status -eq 503 -and ((Get-ErrorCode $mcNoToken) -like "mcp_*")) "(status $($mcNoToken.Status))"
        [void](Invoke-Api -Method POST -Path "$mcBase/$mcClientId/revoke")
        $script:mcpOutcome = "not-running"
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

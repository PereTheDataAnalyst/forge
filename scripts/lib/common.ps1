# Shared helpers for the Forge PowerShell scripts.
# ASCII only. Compatible with Windows PowerShell 5.1 and PowerShell 7.

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}
function Write-Ok([string]$Message) { Write-Host "  [ok]   $Message" -ForegroundColor Green }
function Write-Warn2([string]$Message) { Write-Host "  [warn] $Message" -ForegroundColor Yellow }
function Write-Fail([string]$Message) { Write-Host "  [FAIL] $Message" -ForegroundColor Red }
function Write-Info([string]$Message) { Write-Host "         $Message" }

function New-RandomBytes([int]$Count) {
    $buffer = New-Object byte[] $Count
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($buffer) } finally { $rng.Dispose() }
    return ,$buffer
}

# URL-safe random secret. 48 bytes gives 64 characters.
function New-UrlSafeSecret([int]$Bytes = 48) {
    $raw = New-RandomBytes $Bytes
    $text = [Convert]::ToBase64String($raw)
    return $text.TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

# Letters and digits only, so the value is safe inside URLs and SQL literals.
# Rejection sampling (248 = 62 * 4) removes modulo bias.
function New-AlphaNumericSecret([int]$Length = 32) {
    $alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    $result = New-Object System.Text.StringBuilder
    while ($result.Length -lt $Length) {
        $raw = New-RandomBytes 64
        foreach ($b in $raw) {
            if ($b -lt 248 -and $result.Length -lt $Length) {
                [void]$result.Append($alphabet[$b % 62])
            }
        }
    }
    return $result.ToString()
}

function Get-EnvValue([string]$Path, [string]$Key) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        if ($line -match ('^\s*' + [regex]::Escape($Key) + '=(.*)$')) { return $Matches[1].Trim() }
    }
    return $null
}

# Writes UTF-8 without a byte order mark, which dotenv parsers require.
function Set-EnvValue([string]$Path, [string]$Key, [string]$Value) {
    $lines = New-Object System.Collections.Generic.List[string]
    $found = $false
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
            if ($line -match ('^\s*' + [regex]::Escape($Key) + '=')) {
                $lines.Add($Key + '=' + $Value)
                $found = $true
            } else {
                $lines.Add($line)
            }
        }
    }
    if (-not $found) { $lines.Add($Key + '=' + $Value) }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($Path, $lines.ToArray(), $encoding)
}

function Find-Python {
    foreach ($candidate in @('python', 'py')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($null -eq $cmd) { continue }
        $launcherArgs = @()
        if ($candidate -eq 'py') { $launcherArgs = @('-3') }
        $versionText = (& $cmd.Source @launcherArgs --version 2>&1 | Out-String)
        if ($versionText -match 'Python (\d+)\.(\d+)') {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) {
                return @{ Path = $cmd.Source; LauncherArgs = $launcherArgs; Version = "$major.$minor" }
            }
        }
    }
    return $null
}

function Find-Psql {
    $cmd = Get-Command psql -ErrorAction SilentlyContinue
    if ($null -ne $cmd) { return $cmd.Source }
    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}) | Where-Object { $_ }
    $best = $null
    foreach ($root in $roots) {
        $base = Join-Path $root 'PostgreSQL'
        if (-not (Test-Path -LiteralPath $base)) { continue }
        foreach ($dir in Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue) {
            $exe = Join-Path (Join-Path $dir.FullName 'bin') 'psql.exe'
            if (Test-Path -LiteralPath $exe) {
                $version = 0
                [void][int]::TryParse($dir.Name, [ref]$version)
                if ($null -eq $best -or $version -gt $best.Version) {
                    $best = @{ Path = $exe; Version = $version }
                }
            }
        }
    }
    if ($null -ne $best) { return $best.Path }
    return $null
}

function Test-TcpPort([string]$HostName, [int]$Port, [int]$TimeoutMs = 1500) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) { return $false }
        $client.EndConnect($async)
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Read-PlainPassword([string]$Prompt) {
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    $ptr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

# Runs psql without letting stderr text become a terminating error on Windows PowerShell 5.1.
# Statements that contain secrets are passed through $InputText (stdin), never on the command line.
function Invoke-Psql([string]$Psql, [string[]]$PsqlArgs, [string]$InputText = "") {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($InputText -ne "") {
            $output = $InputText | & $Psql @PsqlArgs 2>$null
        } else {
            $output = & $Psql @PsqlArgs 2>$null
        }
        $code = $LASTEXITCODE
        return @{ ExitCode = $code; Output = (($output | Out-String).Trim()) }
    } finally {
        $ErrorActionPreference = $previous
    }
}

# Runs a command that is expected to succeed and throws otherwise. Never pass secrets in $Arguments.
function Invoke-Native([string]$Exe, [string[]]$Arguments, [string]$WorkingDirectory) {
    Push-Location -LiteralPath $WorkingDirectory
    try {
        & $Exe @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed with exit code ${LASTEXITCODE}: $Exe $($Arguments -join ' ')"
        }
    } finally {
        Pop-Location
    }
}

function Get-VenvPython([string]$ProjectDirectory) {
    return Join-Path (Join-Path (Join-Path $ProjectDirectory '.venv') 'Scripts') 'python.exe'
}

# Creates the worker .env from the example when missing and returns the shared bearer token.
function Initialize-WorkerEnv([string]$WorkerDirectory) {
    $workerEnv = Join-Path $WorkerDirectory ".env"
    if (-not (Test-Path -LiteralPath $workerEnv)) {
        Copy-Item -LiteralPath (Join-Path $WorkerDirectory ".env.example") -Destination $workerEnv
    }
    $token = Get-EnvValue $workerEnv "NATLAS_WORKER_TOKEN"
    if ([string]::IsNullOrWhiteSpace($token) -or $token.StartsWith("CHANGE_ME")) {
        $token = New-UrlSafeSecret 48
        Set-EnvValue $workerEnv "NATLAS_WORKER_TOKEN" $token
    }
    return $token
}

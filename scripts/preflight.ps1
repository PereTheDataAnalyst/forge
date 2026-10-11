<#
.SYNOPSIS
  Read-only inspection of the machine and the Forge repository. It changes nothing.

.DESCRIPTION
  Prints tool versions, service reachability and the Git state of the repository, and saves the
  same report to a text file in %TEMP% so it can be shared. It never prints the contents of .env.

.EXAMPLE
  .\scripts\preflight.ps1 -RepoPath C:\Projects\forge
#>
[CmdletBinding()]
param([string]$RepoPath = "C:\Projects\forge")

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "lib\common.ps1")

$report = New-Object System.Collections.Generic.List[string]
function Add-Line([string]$Text) { $report.Add($Text); Write-Host $Text }

Add-Line "N-ATLAS Forge preflight (read-only) - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Add-Line "PowerShell $($PSVersionTable.PSVersion) on $([System.Environment]::OSVersion.VersionString)"

Write-Step "Tools"
$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if ($null -ne $gitCmd) { Add-Line "git:    $((& git --version) -join ' ')" } else { Add-Line "git:    NOT FOUND" }
$nodeCmd = Get-Command node -ErrorAction SilentlyContinue
if ($null -ne $nodeCmd) { Add-Line "node:   $((& node --version) -join ' ')   (Forge needs 20 or newer)" } else { Add-Line "node:   NOT FOUND" }
$npmCmd = Get-Command npm -ErrorAction SilentlyContinue
if ($null -ne $npmCmd) { Add-Line "npm:    $((& npm --version) -join ' ')" } else { Add-Line "npm:    NOT FOUND" }
$python = Find-Python
if ($null -ne $python) { Add-Line "python: $($python.Version) at $($python.Path)   (Forge needs 3.11 or newer)" } else { Add-Line "python: no suitable Python 3.11+ found" }
$psql = Find-Psql
if ($null -ne $psql) { Add-Line "psql:   $psql" } else { Add-Line "psql:   NOT FOUND (setup-local.ps1 needs it to create the databases)" }

Write-Step "Services"
Add-Line "PostgreSQL localhost:5432 reachable: $(Test-TcpPort 'localhost' 5432)"
Add-Line "Redis/Memurai localhost:6379 reachable: $(Test-TcpPort 'localhost' 6379)"
$memurai = Get-Service -Name 'Memurai*' -ErrorAction SilentlyContinue
if ($null -ne $memurai) { foreach ($s in $memurai) { Add-Line "service $($s.Name): $($s.Status)" } }

Write-Step "Repository: $RepoPath"
if (-not (Test-Path -LiteralPath $RepoPath)) {
    Add-Line "The path does not exist. Clone it with: git clone https://github.com/PereTheDataAnalyst/forge $RepoPath"
} else {
    $insideGit = $false
    if ($null -ne $gitCmd) {
        $probe = (& git -C $RepoPath rev-parse --is-inside-work-tree 2>$null)
        $insideGit = ($probe -eq 'true')
    }
    if (-not $insideGit) {
        Add-Line "The folder exists but is not a Git working tree."
    } else {
        Add-Line "current branch : $((& git -C $RepoPath branch --show-current) -join ' ')"
        Add-Line "remotes        :"
        foreach ($line in (& git -C $RepoPath remote -v)) { Add-Line "  $line" }
        Add-Line "branches       :"
        foreach ($line in (& git -C $RepoPath branch -a)) { Add-Line "  $line" }
        Add-Line "last commits   :"
        foreach ($line in (& git -C $RepoPath log -8 --oneline 2>$null)) { Add-Line "  $line" }
        $status = @(& git -C $RepoPath status --porcelain)
        Add-Line "working tree   : $($status.Count) changed or untracked path(s)"
        foreach ($line in ($status | Select-Object -First 40)) { Add-Line "  $line" }
        if ($status.Count -gt 40) { Add-Line "  ... and $($status.Count - 40) more" }
    }

    Write-Step "Existing project files (names only)"
    $probes = @(
        'README.md', '.gitignore', '.env', '.env.example',
        'backend\pyproject.toml', 'backend\alembic.ini', 'backend\app\main.py',
        'frontend\package.json', 'frontend\package-lock.json', 'frontend\vite.config.ts',
        'inference\natlas-worker\app\main.py', 'docker-compose.yml'
    )
    foreach ($relative in $probes) {
        $exists = Test-Path -LiteralPath (Join-Path $RepoPath $relative)
        Add-Line ("{0,-42} {1}" -f $relative, $(if ($exists) { 'present' } else { 'absent' }))
    }
    $versions = Join-Path $RepoPath 'backend\alembic\versions'
    if (Test-Path -LiteralPath $versions) {
        Add-Line "alembic revisions:"
        foreach ($file in (Get-ChildItem -LiteralPath $versions -Filter '*.py' | Sort-Object Name)) { Add-Line "  $($file.Name)" }
    }
    $modules = Join-Path $RepoPath 'backend\app\modules'
    if (Test-Path -LiteralPath $modules) {
        Add-Line "backend extension modules:"
        foreach ($dir in (Get-ChildItem -LiteralPath $modules -Directory | Where-Object { $_.Name -ne '__pycache__' })) { Add-Line "  $($dir.Name)" }
    }
    $extensions = Join-Path $RepoPath 'frontend\src\extensions'
    if (Test-Path -LiteralPath $extensions) {
        Add-Line "frontend extension modules:"
        foreach ($dir in (Get-ChildItem -LiteralPath $extensions -Directory)) { Add-Line "  $($dir.Name)" }
    }
}

$reportPath = Join-Path $env:TEMP 'forge-preflight-report.txt'
[System.IO.File]::WriteAllLines($reportPath, $report.ToArray(), (New-Object System.Text.UTF8Encoding($false)))
Write-Host ""
Write-Host "Report saved to $reportPath (it contains no secrets)." -ForegroundColor Green

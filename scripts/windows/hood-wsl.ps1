<#
.SYNOPSIS
  Run HOOD inside WSL2 (Linux on Windows) so Python missions get the agent sandbox.
  Chrome on Windows keeps using http://127.0.0.1:8999.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\windows\hood-wsl.ps1
      Copies HOOD into WSL (~/hood), installs packages, checks the sandbox, starts HOOD, opens Chrome.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\windows\hood-wsl.ps1 -InstallWsl
      First time only: installs WSL2 with Ubuntu 24.04 (needs an administrator PowerShell and a restart).

.NOTES
  Stop HOOD with Ctrl+C in this window. Nothing here changes Windows settings unless you pass -InstallWsl.
#>
param(
    [switch]$InstallWsl,
    [switch]$NoStart,
    [switch]$EnableInstalls,
    [int]$Port = 8999,
    [string]$Distro = ""
)
$ErrorActionPreference = "Stop"

function Say($text) { Write-Host "`n== $text" -ForegroundColor Cyan }

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not (Test-Path (Join-Path $repo "hood_cli.py"))) { throw "Run this from the HOOD folder (hood_cli.py not found in $repo)." }

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "WSL is not available on this Windows. Use Windows 10 2004+ or Windows 11, then run with -InstallWsl."
}

if ($InstallWsl) {
    Say "Installing WSL2 with Ubuntu 24.04 (administrator rights needed)"
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) { throw "Open PowerShell as Administrator (right-click > Run as administrator) and run this again with -InstallWsl." }
    wsl.exe --install -d Ubuntu-24.04
    Write-Host "When it finishes: restart Windows, open 'Ubuntu' once from the Start menu to create your Linux user,"
    Write-Host "then run this script again WITHOUT -InstallWsl."
    exit 0
}

# wsl -l -q prints UTF-16; strip NULs so names compare correctly.
$distros = @((wsl.exe -l -q) -replace "`0", "" | Where-Object { $_.Trim() -ne "" } | ForEach-Object { $_.Trim() })
if ($distros.Count -eq 0) {
    throw "No Linux distribution is installed in WSL yet. Run this script once with -InstallWsl (as Administrator)."
}
$wslArgs = @()
if ($Distro) { $wslArgs = @("-d", $Distro) }

Say "Checking that the distribution runs as WSL2"
$listing = @((wsl.exe -l -v) -replace "`0", "")
$listing | ForEach-Object { Write-Host $_ }
foreach ($line in ($listing | Select-Object -Skip 1 | Where-Object { $_.Trim() -ne "" })) {
    $parts = $line.Trim().TrimStart("*").Trim() -split "\s+"
    if ($parts[-1] -eq "1") {
        Write-Warning "$($parts[0]) runs on WSL1. The sandbox needs WSL2:  wsl --set-version $($parts[0]) 2"
    }
}

$wslRepo = (wsl.exe @wslArgs wslpath -a ($repo -replace "\\", "/")).Trim()
Say "HOOD folder as seen from Linux: $wslRepo"

$startFlag = if ($NoStart) { "" } else { "--start" }
if ($EnableInstalls) { $startFlag = "--enable-installs $startFlag" }
if (-not $NoStart) {
    # Open Chrome (default browser) once HOOD answers; runs in the background while HOOD starts here.
    Start-Job -ScriptBlock {
        param($p)
        for ($i = 0; $i -lt 240; $i++) {
            try { Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$p/api/auth/status" -TimeoutSec 2 | Out-Null
                  Start-Process "http://127.0.0.1:$p"; return } catch { Start-Sleep -Seconds 2 }
        }
    } -ArgumentList $Port | Out-Null
}

Say "Installing / updating HOOD inside WSL and starting it (Ctrl+C stops HOOD)"
# The script may have Windows (CRLF) line endings in this checkout: normalise before running it.
$cmd = "tr -d '\r' < '$wslRepo/scripts/wsl/install_hood_wsl.sh' > /tmp/hood_install.sh && " +
       "bash /tmp/hood_install.sh '$wslRepo' $startFlag --port $Port"
wsl.exe @wslArgs -- bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw "The WSL setup stopped with an error (see the messages above)." }

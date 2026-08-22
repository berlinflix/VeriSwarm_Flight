# Pratik Windows: receive Abhijan's five movement-v2 leases and atomically replace
# the authorization snapshot consumed by start_pratik_movement_v2.ps1.

param(
    [string]$BindAddress = "192.168.50.11",
    [string]$MacAddress = "192.168.50.14",
    [int]$Port = 8772,
    [string]$AuthorizationFile = ""
)

$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Workspace = Split-Path $Repo -Parent
$Scratch = Join-Path $Workspace "scratch\pratik\factorycity-disaster\latest"

function Fail([string]$Message) {
    Write-Host "  FAIL  $Message" -ForegroundColor Red
    exit 1
}

if ($BindAddress -ne "192.168.50.11") {
    Fail "receiver must bind Pratik's frozen Ethernet address 192.168.50.11"
}
if ($MacAddress -ne "192.168.50.14") {
    Fail "receiver must accept only Abhijan's frozen Ethernet address 192.168.50.14"
}

$RepoPython = Join-Path $Repo ".venv\Scripts\python.exe"
$CoSimPython = Join-Path $Workspace ".venv-cosys341\Scripts\python.exe"
if (Test-Path $RepoPython) {
    $Python = $RepoPython
    $PythonPrefix = @()
} elseif (Test-Path $CoSimPython) {
    $Python = $CoSimPython
    $PythonPrefix = @()
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $Python = "py"
    $PythonPrefix = @("-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $Python = "python"
    $PythonPrefix = @()
} else {
    Fail "Python 3 was not found; install Python or create .venv before starting the receiver"
}
if (-not (Test-Path (Join-Path $Repo "codebase\tools\movement_authorization_link.py"))) {
    Fail "movement authorization receiver code is missing"
}
if ([string]::IsNullOrWhiteSpace($AuthorizationFile)) {
    $AuthorizationFile = Join-Path $Scratch "authorization_snapshot.json"
}
New-Item -ItemType Directory -Force -Path (Split-Path $AuthorizationFile -Parent) | Out-Null

Write-Host "=== Pratik movement-v2 authorization receiver ===" -ForegroundColor Cyan
Write-Host "  INFO  bind: $BindAddress`:$Port"
Write-Host "  INFO  only accepted peer: $MacAddress"
Write-Host "  INFO  atomic output: $AuthorizationFile"
Write-Host "  INFO  keep this window open before starting movement-v2"

Push-Location (Join-Path $Repo "codebase")
& $Python @PythonPrefix -m tools.movement_authorization_link receive `
    --bind $BindAddress --port $Port `
    --peer-ip $MacAddress `
    --output $AuthorizationFile `
    --mission-id OP-VARUNA-001
$ExitCode = $LASTEXITCODE
Pop-Location
exit $ExitCode

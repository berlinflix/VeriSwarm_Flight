# Pratik Windows PC: continuously drain five durable CoSys producer outboxes directly
# to Abhijan's Mac over the isolated Ethernet link. No bearer token is used or shared.

$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Codebase = Join-Path $Repo "codebase"
$Results = Join-Path $Codebase "results"
$MissionId = "OP-VARUNA-001"

function Fail($Message) {
    Write-Host "  FAIL  $Message" -ForegroundColor Red
    exit 1
}

Write-Host "=== Pratik direct rescue-event sender ===" -ForegroundColor Cyan

$PratikAddress = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -eq '192.168.50.11' } |
    Select-Object -First 1
if ($null -eq $PratikAddress) {
    Fail "frozen private wired IP 192.168.50.11 is not configured"
}
$PratikIp = $PratikAddress.IPAddress
$Octets = $PratikIp.Split('.')
$MacIp = "$($Octets[0]).$($Octets[1]).$($Octets[2]).14"
$Endpoint = "http://${MacIp}:8771"
Write-Host "  PASS  wired Pratik IP $PratikIp" -ForegroundColor Green

$VenvPython = Join-Path $Repo ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $Python = $VenvPython
    $PythonPrefix = @()
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $Python = "py"
    $PythonPrefix = @("-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $Python = "python"
    $PythonPrefix = @()
} else {
    Fail "Python 3 was not found"
}

if (-not (Test-Path (Join-Path $Codebase "tools\rescue_event_sender.py"))) {
    Fail "rescue_event_sender.py is missing; fetch the current Abhijan branch"
}
New-Item -ItemType Directory -Force -Path $Results | Out-Null
Remove-Item Env:VERISWARM_RESCUE_TOKEN -ErrorAction SilentlyContinue

try {
    $Health = Invoke-RestMethod -Method Get -Uri "$Endpoint/health" -TimeoutSec 3
} catch {
    Fail "cannot reach Abhijan ingress at $Endpoint; Abhijan must start the integrated launcher"
}
if ($Health.schema -ne "veriswarm.pratik.ethernet-ingress.v1") {
    Fail "unexpected service at $Endpoint"
}
if ($Health.peer_ip -ne $PratikIp) {
    Fail "Abhijan ingress expects $($Health.peer_ip), but this PC is $PratikIp"
}
Write-Host "  PASS  Abhijan ingress $Endpoint accepts this exact source IP" -ForegroundColor Green
Write-Host "  INFO  waiting for per-node outboxes; Ctrl+C stops delivery only" -ForegroundColor DarkGray

Set-Location $Codebase
while ($true) {
    $Outboxes = @(Get-ChildItem -Path $Results -Filter "*-rescue-outbox.sqlite3" -File -ErrorAction SilentlyContinue)
    foreach ($OutboxFile in $Outboxes) {
        $Outbox = $OutboxFile.FullName
        $Producer = $OutboxFile.BaseName
        & $Python @PythonPrefix -m tools.rescue_event_sender `
            --mission-id $MissionId `
            --outbox $Outbox `
            flush --endpoint $Endpoint --max-events 1000 --timeout 2 `
            --transient-retries 0
        if ($LASTEXITCODE -eq 2) {
            Fail "$Producer has an invalid local event/configuration; preserve it for review"
        }
    }
    if ($Outboxes.Count -eq 0) {
        Write-Host "  WAIT  no *-rescue-outbox.sqlite3 files yet" -ForegroundColor DarkGray
    }
    Start-Sleep -Seconds 1
}

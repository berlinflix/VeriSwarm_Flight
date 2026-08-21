# Start the Bravo verification peer. Run on Samik P2 (192.168.50.12).
# Safe to stop (Ctrl+C) and re-run any number of times: the event filename is
# timestamped, so a restart can never overwrite earlier evidence.

$Repo   = "C:\VeriSwarm\VeriSwarm_SIH"
$IHQ    = "C:\VeriSwarm\IHQ-20260821-001"
$Python = "$Repo\.venv\Scripts\python.exe"

Write-Host "=== Bravo preflight ===" -ForegroundColor Cyan

$fail = $false
function Check($label, $cond) {
    if ($cond) { Write-Host "  PASS  $label" -ForegroundColor Green }
    else       { Write-Host "  FAIL  $label" -ForegroundColor Red; $script:fail = $true }
}

Check "python venv"           (Test-Path $Python)
Check "bravo manifest"        (Test-Path "$IHQ\bravo-private\bravo.manifest.json")
Check "bravo fixture"         (Test-Path "$IHQ\fixtures\bravo\snapshot.json")
# Bravo must hold nobody else's signing key.
Check "no alpha-private here" (-not (Test-Path "$IHQ\alpha-private"))
Check "no charlie-private here" (-not (Test-Path "$IHQ\charlie-private"))

$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
       Where-Object { $_.IPAddress -eq "192.168.50.12" })
Check "static IP 192.168.50.12" ($null -ne $ip)

# Bravo disciplines to Charlie. Past 250 ms of skew this peer abstains
# (ok_no_observation) and the clean case silently loses a semantic ACK.
#
# Advisory only, never fatal: `w32tm /query` needs elevation, so a normal-user
# launch cannot read it. The authoritative gate is Alpha's `run_model_hash.sh`,
# which measures the real offset over the Ping RPC and refuses to run past
# 250 ms. Blocking a peer here on an unreadable diagnostic would stop the demo
# for no reason.
$src = $null
try { $src = (w32tm /query /source 2>&1 | Out-String).Trim() } catch { }
if ($src -match "192\.168\.50\.13") {
    Write-Host "  PASS  time source is Charlie (192.168.50.13)" -ForegroundColor Green
} elseif ([string]::IsNullOrWhiteSpace($src) -or $src -match "Access is denied|0x80070005") {
    Write-Host "  SKIP  time source unreadable (needs Administrator)" -ForegroundColor DarkGray
    Write-Host "        Alpha's run_model_hash.sh measures the real offset." -ForegroundColor DarkGray
} else {
    Write-Host "  WARN  time source is not Charlie: $src" -ForegroundColor Yellow
    Write-Host "        fix (as Administrator): w32tm /resync /force" -ForegroundColor Yellow
}

# gRPC reports a bind clash as an opaque "No address added" traceback, so name
# the real cause here: an already-running Bravo is the usual reason.
$inUse = Get-NetTCPConnection -LocalPort 51001 -State Listen -ErrorAction SilentlyContinue
if ($inUse) {
    Write-Host "  FAIL  port 51001 is already in use" -ForegroundColor Red
    Write-Host "        Bravo is probably already running in another window." -ForegroundColor Yellow
    Write-Host "        Use that window, or stop it with Ctrl+C and re-run this script." -ForegroundColor Yellow
    $fail = $true
} else {
    Write-Host "  PASS  port 51001 free" -ForegroundColor Green
}

if ($fail) {
    Write-Host "`nPreflight failed - not starting Bravo." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

$S      = "bravo-" + (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmss")
$Events = "$IHQ\evidence\protocol\$S.jsonl"
New-Item -ItemType Directory -Force -Path (Split-Path $Events) | Out-Null

Write-Host "`nStarting Bravo on :51001  (Ctrl+C to stop)" -ForegroundColor Cyan
Write-Host "  events -> $Events`n"

Set-Location "$Repo\codebase"
& $Python -m tools.qualification_peer `
    --manifest "$IHQ\bravo-private\bravo.manifest.json" `
    --id bravo `
    --fixture "$IHQ\fixtures\bravo\snapshot.json" `
    --events $Events `
    --verbose

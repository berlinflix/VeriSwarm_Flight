# Start the Charlie verification peer. Run on Suyash L1 (192.168.50.13).
# Safe to stop (Ctrl+C) and re-run any number of times: the event filename is
# timestamped, so a restart can never overwrite earlier evidence.

$Repo    = "C:\Users\suyas\sih\.codex-worktrees\model-hash-4483d87"
$IHQ     = "C:\VeriSwarm\IHQ-20260821-001"
$Python  = "$Repo\.venv\Scripts\python.exe"

Write-Host "=== Charlie preflight ===" -ForegroundColor Cyan

$fail = $false
function Check($label, $cond) {
    if ($cond) { Write-Host "  PASS  $label" -ForegroundColor Green }
    else       { Write-Host "  FAIL  $label" -ForegroundColor Red; $script:fail = $true }
}

Check "python venv"            (Test-Path $Python)
Check "charlie manifest"       (Test-Path "$IHQ\charlie-private\charlie.manifest.json")
Check "charlie fixture"        (Test-Path "$IHQ\fixtures\charlie\snapshot.json")
# Charlie must hold nobody else's signing key.
Check "no alpha-private here"  (-not (Test-Path "$IHQ\alpha-private"))
Check "no bravo-private here"  (-not (Test-Path "$IHQ\bravo-private"))

$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
       Where-Object { $_.IPAddress -eq "192.168.50.13" })
Check "static IP 192.168.50.13" ($null -ne $ip)

# Charlie is the LAN time source: Alpha and Bravo both discipline to it, and a
# peer whose clock drifts past 250 ms silently abstains instead of verifying.
$ntp = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Config" -Name AnnounceFlags -ErrorAction SilentlyContinue).AnnounceFlags
Check "NTP server announcing (AnnounceFlags=5)" ($ntp -eq 5)
Check "w32time running"        ((Get-Service w32time).Status -eq "Running")

# gRPC reports a bind clash as an opaque "No address added" traceback, so name
# the real cause here: an already-running Charlie is the usual reason.
$inUse = Get-NetTCPConnection -LocalPort 51003 -State Listen -ErrorAction SilentlyContinue
if ($inUse) {
    Write-Host "  FAIL  port 51003 is already in use" -ForegroundColor Red
    Write-Host "        Charlie is probably already running in another window." -ForegroundColor Yellow
    Write-Host "        Use that window, or stop it with Ctrl+C and re-run this script." -ForegroundColor Yellow
    $fail = $true
} else {
    Write-Host "  PASS  port 51003 free" -ForegroundColor Green
}

if ($fail) {
    Write-Host "`nPreflight failed - not starting Charlie." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

$S      = "charlie-" + (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmss")
$Events = "$IHQ\evidence\protocol\$S.jsonl"
New-Item -ItemType Directory -Force -Path (Split-Path $Events) | Out-Null

Write-Host "`nStarting Charlie on :51003  (Ctrl+C to stop)" -ForegroundColor Cyan
Write-Host "  events -> $Events`n"

Set-Location "$Repo\codebase"
& $Python -m tools.qualification_peer `
    --manifest "$IHQ\charlie-private\charlie.manifest.json" `
    --id charlie `
    --fixture "$IHQ\fixtures\charlie\snapshot.json" `
    --events $Events `
    --verbose

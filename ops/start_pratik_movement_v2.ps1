# Start Pratik's additive live movement-v2 controller. The accepted nominal-v1
# launcher and controller are intentionally untouched.

param(
    [string]$ProjectRoot = "",
    [string]$AuthorizationFile = "",
    [string]$OutboxDirectory = "",
    [string]$RunResult = "",
    [ValidateSet("sensor", "authorized-nominal")]
    [string]$RouteMode = "sensor"
)

$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Workspace = Split-Path $Repo -Parent
$FactoryCity = Join-Path $Repo "codebase\sim\cosys\factorycity"

function Fail([string]$Message) {
    Write-Host "  FAIL  $Message" -ForegroundColor Red
    exit 1
}

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectCandidates = @(
        "D:\VeriSwarm_FactoryCity_UE5.8.1_Phase3",
        (Join-Path $Workspace "VeriSwarm_FactoryCity_UE5.8.1_Phase3")
    )
    $ProjectRoot = $ProjectCandidates |
        Where-Object { Test-Path (Join-Path $_ "MyProject.uproject") } |
        Select-Object -First 1
}
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    Fail "FactoryCity project was not found; pass -ProjectRoot"
}
$ProjectRoot = (Resolve-Path $ProjectRoot).Path

$PythonCandidates = @(
    (Join-Path $Repo ".venv\Scripts\python.exe"),
    (Join-Path $Workspace ".venv-cosys341\Scripts\python.exe")
)
$Python = $PythonCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($null -eq $Python) {
    Fail "CoSim Python environment was not found"
}

$Scratch = Join-Path $Workspace "scratch\pratik\factorycity-disaster\latest"
if ([string]::IsNullOrWhiteSpace($AuthorizationFile)) {
    $AuthorizationFile = Join-Path $Scratch "authorization_snapshot.json"
}
if ([string]::IsNullOrWhiteSpace($OutboxDirectory)) {
    $OutboxDirectory = Join-Path $Repo "codebase\results"
}
if ([string]::IsNullOrWhiteSpace($RunResult)) {
    $RunResult = Join-Path $Scratch "movement_v2_run.json"
}

$Inputs = [ordered]@{
    "runner" = Join-Path $FactoryCity "tools\run_factorycity_movement_v2.py"
    "mission config" = Join-Path $FactoryCity "factorycity_ab_mission.development.json"
    "movement contract" = Join-Path $FactoryCity "factorycity_joint_movement_contract.development.json"
    "movement extension" = Join-Path $FactoryCity "factorycity_sensor_movement_extension.v2.development.json"
    "cell extension" = Join-Path $Repo "codebase\config\rescue_cell_movement_event_extension.v1.json"
    "map" = Join-Path $ProjectRoot "Content\VeriSwarm\FactoryCity_Disaster.umap"
    "layer result" = Join-Path $Scratch "rising_flood_layer_result.json"
    "authorization snapshot" = $AuthorizationFile
}
foreach ($Entry in $Inputs.GetEnumerator()) {
    if (-not (Test-Path -LiteralPath $Entry.Value -PathType Leaf)) {
        Fail "$($Entry.Key) is missing: $($Entry.Value)"
    }
}

New-Item -ItemType Directory -Force -Path $OutboxDirectory | Out-Null
$RunParent = Split-Path $RunResult -Parent
if (-not [string]::IsNullOrWhiteSpace($RunParent)) {
    New-Item -ItemType Directory -Force -Path $RunParent | Out-Null
}

Write-Host "=== Pratik FactoryCity live movement-v2 ===" -ForegroundColor Cyan
Write-Host "  PASS  nominal-v1 remains unchanged" -ForegroundColor Green
Write-Host "  INFO  FactoryCity_Disaster must already be running in Play mode"
Write-Host "  INFO  $AuthorizationFile must be atomically refreshed with all five leases"
Write-Host "  INFO  every lease must remain newer than the frozen two-second timeout"
Write-Host "  INFO  durable events will be written to $OutboxDirectory"
Write-Host "  INFO  route mode: $RouteMode"
if ($RouteMode -eq "authorized-nominal") {
    Write-Host "  INFO  movement-first A-to-B path; do not claim obstacle deflection from this run" -ForegroundColor Yellow
}

Push-Location (Join-Path $Repo "codebase")
& $Python -m sim.cosys.factorycity.tools.run_factorycity_movement_v2 `
    --mission-config $Inputs["mission config"] `
    --movement-contract $Inputs["movement contract"] `
    --movement-extension $Inputs["movement extension"] `
    --cell-extension $Inputs["cell extension"] `
    --map-file $Inputs["map"] `
    --layer-result $Inputs["layer result"] `
    --authorization-file $Inputs["authorization snapshot"] `
    --outbox-directory $OutboxDirectory `
    --route-mode $RouteMode `
    --output $RunResult
$RunExit = $LASTEXITCODE
Pop-Location
if ($RunExit -eq 0) {
    Write-Host "  PASS  movement-v2 result: $RunResult" -ForegroundColor Green
} else {
    Write-Host "  FAIL  movement-v2 exited $RunExit; review $RunResult" -ForegroundColor Red
}
exit $RunExit

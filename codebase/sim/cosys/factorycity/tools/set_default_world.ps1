[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedCurrentWorld,

    [Parameter(Mandatory = $true)]
    [string]$TargetWorld,

    [Parameter(Mandatory = $true)]
    [string]$TargetMapFile,

    [Parameter(Mandatory = $true)]
    [string]$BackupFile,

    [Parameter(Mandatory = $true)]
    [string]$OperationRecord
)

$ErrorActionPreference = 'Stop'

$running = @(Get-Process -Name UnrealEditor, UnrealEditor-Cmd -ErrorAction SilentlyContinue)
if ($running.Count -gt 0) {
    throw "Close every Unreal Editor process before changing the project default world."
}

$root = (Resolve-Path -LiteralPath $ProjectRoot).Path
$config = Join-Path $root 'Config\DefaultEngine.ini'
$targetMap = (Resolve-Path -LiteralPath $TargetMapFile).Path
if (-not (Test-Path -LiteralPath $config -PathType Leaf)) {
    throw "DefaultEngine.ini not found: $config"
}
if (-not (Test-Path -LiteralPath $targetMap -PathType Leaf)) {
    throw "target world file not found: $targetMap"
}

$paths = @($config, $targetMap, $BackupFile, $OperationRecord) | ForEach-Object {
    [System.IO.Path]::GetFullPath($_)
}
if (($paths | Sort-Object -Unique).Count -ne $paths.Count) {
    throw 'Config, target map, backup, and operation-record paths must be distinct.'
}
if (Test-Path -LiteralPath $BackupFile) {
    throw "refusing to overwrite default-world backup: $BackupFile"
}
if (Test-Path -LiteralPath $OperationRecord) {
    throw "refusing to overwrite operation record: $OperationRecord"
}

$text = [System.IO.File]::ReadAllText($config)
$editorSource = "EditorStartupMap=$ExpectedCurrentWorld.$(($ExpectedCurrentWorld -split '/')[-1])"
$gameSource = "GameDefaultMap=$ExpectedCurrentWorld.$(($ExpectedCurrentWorld -split '/')[-1])"
$editorTarget = "EditorStartupMap=$TargetWorld.$(($TargetWorld -split '/')[-1])"
$gameTarget = "GameDefaultMap=$TargetWorld.$(($TargetWorld -split '/')[-1])"
if (-not $text.Contains($editorSource) -or -not $text.Contains($gameSource)) {
    throw "expected current-world entries were not both present in $config"
}
if (-not $text.Contains('GlobalDefaultGameMode=/Script/AirSim.AirSimGameMode')) {
    throw 'AirSim global game mode is not configured; refusing to switch maps.'
}

$backupParent = Split-Path -Parent $BackupFile
$recordParent = Split-Path -Parent $OperationRecord
New-Item -ItemType Directory -Path $backupParent -Force | Out-Null
New-Item -ItemType Directory -Path $recordParent -Force | Out-Null
$beforeHash = (Get-FileHash -LiteralPath $config -Algorithm SHA256).Hash
$temporary = "$config.veriswarm.tmp"
$record = [ordered]@{
    schema = 'veriswarm.factorycity.default_world_switch.v1'
    status = 'PENDING'
    captured_at_utc = [DateTime]::UtcNow.ToString('o')
    config_file = $config
    config_sha256_before = $beforeHash
    config_sha256_after = $null
    backup_file = [System.IO.Path]::GetFullPath($BackupFile)
    backup_sha256 = (Get-FileHash -LiteralPath $BackupFile -Algorithm SHA256).Hash
    expected_current_world = $ExpectedCurrentWorld
    target_world = $TargetWorld
    target_map_file = $targetMap
    target_map_development_sha256 = (Get-FileHash -LiteralPath $targetMap -Algorithm SHA256).Hash
    airsim_game_mode = '/Script/AirSim.AirSimGameMode'
}
$record | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $OperationRecord -Encoding utf8NoBOM
try {
    [System.IO.File]::Copy($config, $BackupFile, $false)
    $updated = $text.Replace($editorSource, $editorTarget).Replace($gameSource, $gameTarget)
    [System.IO.File]::WriteAllText($temporary, $updated, [System.Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $config -Force
    $afterText = [System.IO.File]::ReadAllText($config)
    if (-not $afterText.Contains($editorTarget) -or -not $afterText.Contains($gameTarget)) {
        throw 'default-world update did not persist both required entries'
    }
    $record.status = 'PASS'
    $record.config_sha256_after = (Get-FileHash -LiteralPath $config -Algorithm SHA256).Hash
}
catch {
    $record.status = 'FAIL'
    $record.error = $_.Exception.Message
    throw
}
finally {
    if (Test-Path -LiteralPath $temporary -PathType Leaf) {
        Remove-Item -LiteralPath $temporary -Force
    }
    $record | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $OperationRecord -Encoding utf8NoBOM
}
$record | ConvertTo-Json -Depth 4

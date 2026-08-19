[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PythonPath,
    [string]$WeightsPath,
    [string]$CameraA = "0",
    [ValidateSet("auto", "dshow", "msmf", "ffmpeg")]
    [string]$CameraABackend = "dshow",
    [string]$CameraB = "2",
    [ValidateSet("auto", "dshow", "msmf", "ffmpeg")]
    [string]$CameraBBackend = "msmf",
    [string]$ShortcutName = "VeriSwarm Semantic Camera Demo",
    [string]$ShortcutDirectory,
    [switch]$RecordVideo,
    [switch]$SkipDroidCamProcessCheck,
    [switch]$Force,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
$launcher = Join-Path $PSScriptRoot "launch_covis_semantic_demo.ps1"
$codebase = Split-Path -Parent $PSScriptRoot

$PythonPath = (Resolve-Path -LiteralPath $PythonPath -ErrorAction Stop).Path
if ([string]::IsNullOrWhiteSpace($WeightsPath)) {
    $WeightsPath = Join-Path $codebase "yolov8n.pt"
}
$WeightsPath = (Resolve-Path -LiteralPath $WeightsPath -ErrorAction Stop).Path

& $launcher -PythonPath $PythonPath -WeightsPath $WeightsPath -ValidateOnly
if ($LASTEXITCODE -ne 0) {
    throw "Portable launcher validation failed with exit code $LASTEXITCODE"
}

if ([string]::IsNullOrWhiteSpace($ShortcutDirectory)) {
    $ShortcutDirectory = [Environment]::GetFolderPath("Desktop")
}
if (-not (Test-Path -LiteralPath $ShortcutDirectory -PathType Container)) {
    throw "Shortcut directory does not exist: $ShortcutDirectory"
}

$shortcutPath = Join-Path $ShortcutDirectory ($ShortcutName + ".lnk")
if ($ValidateOnly) {
    Write-Host "Shortcut installer validation: PASS" -ForegroundColor Green
    Write-Host "Shortcut would be created at: $shortcutPath"
    exit 0
}

if (Test-Path -LiteralPath $shortcutPath) {
    if (-not $Force) {
        throw "Shortcut already exists: $shortcutPath. Pass -Force only when replacing that intended shortcut."
    }
}

function Quote-ShortcutArgument {
    param([string]$Value)
    return '"' + $Value.Replace('"', '""') + '"'
}

$powershellPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$arguments = @(
    "-NoLogo",
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", (Quote-ShortcutArgument $launcher),
    "-PythonPath", (Quote-ShortcutArgument $PythonPath),
    "-WeightsPath", (Quote-ShortcutArgument $WeightsPath),
    "-CameraA", (Quote-ShortcutArgument $CameraA),
    "-CameraABackend", $CameraABackend,
    "-CameraB", (Quote-ShortcutArgument $CameraB),
    "-CameraBBackend", $CameraBBackend
)
if ($RecordVideo) {
    $arguments += "-RecordVideo"
}
if ($SkipDroidCamProcessCheck) {
    $arguments += "-SkipDroidCamProcessCheck"
}

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $powershellPath
$shortcut.Arguments = $arguments -join " "
$shortcut.WorkingDirectory = $codebase
$shortcut.Description = "Launch the VeriSwarm USB plus DroidCam semantic three-panel demonstration"
$shortcut.IconLocation = "$env:SystemRoot\System32\imageres.dll,174"
$shortcut.Save()

Write-Host "Shortcut created: $shortcutPath" -ForegroundColor Green
Write-Host "The shortcut contains local paths only; do not commit the .lnk file."

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$CameraConfigPath,
    [Parameter(Mandatory = $true)]
    [string]$PythonPath,
    [string]$WeightsPath,
    [string]$ShortcutName = "VeriSwarm Multi-Camera Demo",
    [string]$ShortcutDirectory,
    [switch]$FeatureOnly,
    [switch]$Force,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
$launcher = Join-Path $PSScriptRoot "launch_covis_multicam_demo.ps1"
$codebase = Split-Path -Parent $PSScriptRoot
$CameraConfigPath = (Resolve-Path -LiteralPath $CameraConfigPath -ErrorAction Stop).Path
$PythonPath = (Resolve-Path -LiteralPath $PythonPath -ErrorAction Stop).Path
if (-not (Test-Path -LiteralPath $CameraConfigPath -PathType Leaf)) {
    throw "Camera configuration path is not a file: $CameraConfigPath"
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python path is not a file: $PythonPath"
}
if (-not $FeatureOnly) {
    if ([string]::IsNullOrWhiteSpace($WeightsPath)) {
        $WeightsPath = Join-Path $codebase "yolov8n.pt"
    }
    $WeightsPath = (Resolve-Path -LiteralPath $WeightsPath -ErrorAction Stop).Path
    if (-not (Test-Path -LiteralPath $WeightsPath -PathType Leaf)) {
        throw "Weights path is not a file: $WeightsPath"
    }
}

$validation = @{
    CameraConfigPath = $CameraConfigPath
    PythonPath = $PythonPath
    ValidateOnly = $true
}
if ($FeatureOnly) {
    $validation.FeatureOnly = $true
}
else {
    $validation.WeightsPath = $WeightsPath
}
& $launcher @validation
if ($LASTEXITCODE -ne 0) {
    throw "Multicamera launcher validation failed with exit code $LASTEXITCODE"
}

if ([string]::IsNullOrWhiteSpace($ShortcutDirectory)) {
    $ShortcutDirectory = [Environment]::GetFolderPath("Desktop")
}
if (-not (Test-Path -LiteralPath $ShortcutDirectory -PathType Container)) {
    throw "Shortcut directory does not exist: $ShortcutDirectory"
}
$shortcutPath = Join-Path $ShortcutDirectory ($ShortcutName + ".lnk")

if ($ValidateOnly) {
    Write-Host "Multicamera shortcut installer validation: PASS" -ForegroundColor Green
    Write-Host "Shortcut would be created at: $shortcutPath"
    exit 0
}
if ((Test-Path -LiteralPath $shortcutPath) -and -not $Force) {
    throw "Shortcut already exists: $shortcutPath. Pass -Force only to replace that intended shortcut."
}

function Quote-ShortcutArgument {
    param([string]$Value)
    return '"' + $Value.Replace('"', '""') + '"'
}

$powershellPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$arguments = @(
    "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", (Quote-ShortcutArgument $launcher),
    "-CameraConfigPath", (Quote-ShortcutArgument $CameraConfigPath),
    "-PythonPath", (Quote-ShortcutArgument $PythonPath)
)
if ($FeatureOnly) {
    $arguments += "-FeatureOnly"
}
else {
    $arguments += @("-WeightsPath", (Quote-ShortcutArgument $WeightsPath))
}

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $powershellPath
$shortcut.Arguments = $arguments -join " "
$shortcut.WorkingDirectory = $codebase
$shortcut.Description = "Launch the VeriSwarm variable 2-to-5-camera dashboard"
$shortcut.IconLocation = "$env:SystemRoot\System32\imageres.dll,174"
$shortcut.Save()

Write-Host "Shortcut created: $shortcutPath" -ForegroundColor Green
Write-Host "Edit the local JSON configuration to change cameras; never commit private URLs or the .lnk."

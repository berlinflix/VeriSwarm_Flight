[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$CameraConfigPath,
    [string]$PythonPath,
    [string]$WeightsPath,
    [switch]$FeatureOnly,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
$expectedModelSha256 = "F59B3D833E2FF32E194B5BB8E08D211DC7C5BDF144B90D2C8412C47CCFC83B36"
$expectedSchema = "veriswarm.covis_multicam.launch.v1"
$allowedBackends = @("auto", "dshow", "msmf", "ffmpeg")
$codebase = Split-Path -Parent $PSScriptRoot
$repositoryRoot = Split-Path -Parent $codebase

function Resolve-PythonPath {
    param([string]$RequestedPath)

    if (-not [string]::IsNullOrWhiteSpace($RequestedPath)) {
        return (Resolve-Path -LiteralPath $RequestedPath -ErrorAction Stop).Path
    }

    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($env:VIRTUAL_ENV)) {
        $candidates += (Join-Path $env:VIRTUAL_ENV "Scripts\python.exe")
    }
    $candidates += (Join-Path $repositoryRoot ".venv-covis-semantic-py312-cu130\Scripts\python.exe")
    $candidates += (Join-Path $codebase ".venv-covis-semantic-py312-cu130\Scripts\python.exe")

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw "Approved Python was not found. Pass -PythonPath with the frozen environment's python.exe."
}

function Get-NumberSetting {
    param(
        [object]$Config,
        [string]$Name,
        [double]$Default,
        [double]$Minimum
    )

    $property = $Config.PSObject.Properties[$Name]
    $value = if ($null -eq $property) { $Default } else { [double]$property.Value }
    if ([double]::IsNaN($value) -or [double]::IsInfinity($value) -or $value -lt $Minimum) {
        throw "Configuration value $Name must be finite and at least $Minimum."
    }
    return $value
}

function Convert-Invariant {
    param([double]$Value)
    return $Value.ToString([System.Globalization.CultureInfo]::InvariantCulture)
}

$PythonPath = Resolve-PythonPath -RequestedPath $PythonPath
$CameraConfigPath = (Resolve-Path -LiteralPath $CameraConfigPath -ErrorAction Stop).Path
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python path is not a file: $PythonPath"
}
if (-not (Test-Path -LiteralPath $CameraConfigPath -PathType Leaf)) {
    throw "Camera configuration path is not a file: $CameraConfigPath"
}
try {
    $config = Get-Content -LiteralPath $CameraConfigPath -Raw | ConvertFrom-Json -ErrorAction Stop
}
catch {
    throw "Camera configuration is not complete valid JSON: $($_.Exception.Message)"
}

if ($config.schema -ne $expectedSchema) {
    throw "Camera configuration schema must be exactly $expectedSchema."
}

$cameraValues = @($config.cameras)
if ($cameraValues.Count -lt 2 -or $cameraValues.Count -gt 5) {
    throw "Camera configuration must contain between 2 and 5 cameras."
}

$cameraSpecs = @()
$names = @{}
$sources = @{}
foreach ($camera in $cameraValues) {
    $name = [string]$camera.name
    $source = [string]$camera.source
    $backend = ([string]$camera.backend).ToLowerInvariant()
    if ($name -notmatch '^[A-Za-z0-9._-]+$') {
        throw "Invalid camera name '$name'. Use letters, digits, dot, dash or underscore."
    }
    if ([string]::IsNullOrWhiteSpace($source)) {
        throw "Camera $name has an empty source."
    }
    if ($source -match '(?i)(REPLACE|PHONE_[0-9]+|VIDEO_PATH|PORT|[<>])') {
        throw "Camera $name still contains a placeholder source: $source"
    }
    if ($backend -notin $allowedBackends) {
        throw "Camera $name uses unsupported backend '$backend'."
    }
    if ($names.ContainsKey($name)) {
        throw "Duplicate camera name: $name"
    }
    $sourceKey = if ($source -match '^\d+$') {
        "index:$([int64]$source)"
    }
    else {
        "string:$source"
    }
    if ($sources.ContainsKey($sourceKey)) {
        throw "Duplicate camera source: $source"
    }
    $names[$name] = $true
    $sources[$sourceKey] = $true
    $cameraSpecs += [pscustomobject]@{
        Name = $name
        Source = $source
        Backend = $backend
    }
}

$width = [int](Get-NumberSetting $config "width" 640 1)
$height = [int](Get-NumberSetting $config "height" 360 1)
$fps = Get-NumberSetting $config "fps" 15 0.01
$analysisFps = Get-NumberSetting $config "analysis_fps" 3 0.01
$releaseTimeout = Get-NumberSetting $config "release_timeout" 20 1
$displayWidth = [int](Get-NumberSetting $config "display_width" 1600 800)
$displayHeight = [int](Get-NumberSetting $config "display_height" 900 600)
$videoFps = Get-NumberSetting $config "video_fps" $analysisFps 0.01
$recordVideo = $config.record_video -eq $true
$videoCodec = if ($null -eq $config.video_codec) { "MJPG" } else { [string]$config.video_codec }
if ($videoCodec.Length -ne 4 -or $videoCodec -notmatch '^[\x20-\x7E]{4}$') {
    throw "video_codec must contain exactly four printable ASCII characters."
}
$runIdPrefix = if ($null -eq $config.run_id_prefix) { "DEMO-MULTICAM-P2" } else { [string]$config.run_id_prefix }
if ($runIdPrefix -notmatch '^[A-Za-z0-9._-]+$') {
    throw "run_id_prefix contains unsupported characters."
}

$actualModelSha256 = $null
if (-not $FeatureOnly) {
    if ([string]::IsNullOrWhiteSpace($WeightsPath)) {
        $WeightsPath = Join-Path $codebase "yolov8n.pt"
    }
    $WeightsPath = (Resolve-Path -LiteralPath $WeightsPath -ErrorAction Stop).Path
    if (-not (Test-Path -LiteralPath $WeightsPath -PathType Leaf)) {
        throw "Weights path is not a file: $WeightsPath"
    }
    $actualModelSha256 = (Get-FileHash -LiteralPath $WeightsPath -Algorithm SHA256).Hash
    if ($actualModelSha256 -ne $expectedModelSha256) {
        throw "YOLO model hash mismatch. Expected $expectedModelSha256 but found $actualModelSha256"
    }
}

if ($ValidateOnly) {
    & $PythonPath -c "import cv2, numpy; print('Core camera imports: PASS'); print('OpenCV:', cv2.__version__); print('NumPy:', numpy.__version__)"
    if ($LASTEXITCODE -ne 0) {
        throw "Python camera import validation failed with exit code $LASTEXITCODE"
    }
    if (-not $FeatureOnly) {
        & $PythonPath -c "import torch; import ultralytics; print('Semantic imports: PASS'); print('Torch:', torch.__version__, 'CUDA:', torch.cuda.is_available()); print('Ultralytics:', ultralytics.__version__)"
        if ($LASTEXITCODE -ne 0) {
            throw "Python semantic import validation failed with exit code $LASTEXITCODE"
        }
    }

    Write-Host "Multicamera launcher validation: PASS" -ForegroundColor Green
    Write-Host "Python: $PythonPath"
    Write-Host "Configuration: $CameraConfigPath"
    Write-Host "Cameras: $($cameraSpecs.Count); pairs: $(($cameraSpecs.Count * ($cameraSpecs.Count - 1)) / 2)"
    foreach ($camera in $cameraSpecs) {
        Write-Host "  $($camera.Name): $($camera.Source) [$($camera.Backend)]"
    }
    if ($FeatureOnly) {
        Write-Host "Mode: feature-only"
    }
    else {
        Write-Host "Mode: semantic YOLO"
        Write-Host "Weights: $WeightsPath"
        Write-Host "Model SHA-256: $actualModelSha256"
    }
    exit 0
}

$runId = "{0}-{1}-{2}" -f $runIdPrefix, (Get-Date -Format "yyyyMMdd-HHmmss-fff"), ([guid]::NewGuid().ToString("N").Substring(0, 6))
$runnerArguments = @("-m", "tools.covis_multicam")
foreach ($camera in $cameraSpecs) {
    $runnerArguments += @("--camera", $camera.Name, $camera.Source, $camera.Backend)
}
$runnerArguments += @(
    "--width", $width.ToString(),
    "--height", $height.ToString(),
    "--fps", (Convert-Invariant $fps),
    "--analysis-fps", (Convert-Invariant $analysisFps),
    "--release-timeout", (Convert-Invariant $releaseTimeout),
    "--display-width", $displayWidth.ToString(),
    "--display-height", $displayHeight.ToString(),
    "--run-id", $runId
)
if ($recordVideo) {
    $runnerArguments += @(
        "--record-video",
        "--video-codec", $videoCodec,
        "--video-fps", (Convert-Invariant $videoFps)
    )
}
if (-not $FeatureOnly) {
    $runnerArguments += @(
        "--weights", $WeightsPath,
        "--expected-model-sha256", $expectedModelSha256,
        "--confidence", "0.25"
    )
}

Write-Host "Starting VeriSwarm multi-camera dashboard" -ForegroundColor Cyan
Write-Host "Run ID: $runId"
Write-Host "Cameras: $($cameraSpecs.Count); pairs: $(($cameraSpecs.Count * ($cameraSpecs.Count - 1)) / 2)"
Write-Host "Controls: s = save dashboard, q = quit and release every source"
Write-Host "This is an experimental demonstration, not accepted qualification evidence." -ForegroundColor Yellow

Push-Location $codebase
try {
    & $PythonPath @runnerArguments
    $runnerExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($runnerExitCode -ne 0) {
    Write-Host "Multicamera demo exited with code $runnerExitCode. Preserve its create-once run directory." -ForegroundColor Red
    Read-Host "Press Enter to close"
}
exit $runnerExitCode

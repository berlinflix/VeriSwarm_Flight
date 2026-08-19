[CmdletBinding()]
param(
    [string]$PythonPath,
    [string]$WeightsPath,
    [string]$CameraA = "0",
    [string]$CameraAName = "usb_webcam",
    [ValidateSet("auto", "dshow", "msmf", "ffmpeg")]
    [string]$CameraABackend = "dshow",
    [string]$CameraB = "2",
    [string]$CameraBName = "android_droidcam",
    [ValidateSet("auto", "dshow", "msmf", "ffmpeg")]
    [string]$CameraBBackend = "msmf",
    [ValidateRange(1, 7680)]
    [int]$Width = 640,
    [ValidateRange(1, 4320)]
    [int]$Height = 360,
    [ValidateRange(1, 240)]
    [int]$Fps = 15,
    [ValidateRange(0.01, 1.0)]
    [double]$Confidence = 0.25,
    [ValidateRange(1, 120)]
    [int]$ReleaseTimeout = 20,
    [string]$RunIdPrefix = "DEMO-OVERLAP-P2",
    [switch]$RecordVideo,
    [switch]$FeatureOnly,
    [switch]$SkipDroidCamProcessCheck,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
$expectedModelSha256 = "F59B3D833E2FF32E194B5BB8E08D211DC7C5BDF144B90D2C8412C47CCFC83B36"
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

    throw "Semantic Python was not found. Pass -PythonPath with the approved environment's python.exe."
}

$PythonPath = Resolve-PythonPath -RequestedPath $PythonPath
if ([string]::IsNullOrWhiteSpace($WeightsPath)) {
    $WeightsPath = Join-Path $codebase "yolov8n.pt"
}

if (-not (Test-Path -LiteralPath (Join-Path $codebase "tools\covis_live.py") -PathType Leaf)) {
    throw "Co-visibility runner was not found under: $codebase"
}

$actualModelSha256 = $null
if (-not $FeatureOnly) {
    $WeightsPath = (Resolve-Path -LiteralPath $WeightsPath -ErrorAction Stop).Path
    $actualModelSha256 = (Get-FileHash -LiteralPath $WeightsPath -Algorithm SHA256).Hash
    if ($actualModelSha256 -ne $expectedModelSha256) {
        throw "YOLO model hash mismatch. Expected $expectedModelSha256 but found $actualModelSha256"
    }
}

if ($ValidateOnly) {
    & $PythonPath -c "import cv2, numpy, torch; import ultralytics; print('Python semantic imports: PASS'); print('Torch:', torch.__version__, 'CUDA:', torch.cuda.is_available()); print('Ultralytics:', ultralytics.__version__); print('OpenCV:', cv2.__version__); print('NumPy:', numpy.__version__)"
    if ($LASTEXITCODE -ne 0) {
        throw "Semantic Python import validation failed with exit code $LASTEXITCODE"
    }

    Write-Host "Launcher validation: PASS" -ForegroundColor Green
    Write-Host "Python: $PythonPath"
    Write-Host "Codebase: $codebase"
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

if (-not $SkipDroidCamProcessCheck -and -not (Get-Process -Name "droidcam" -ErrorAction SilentlyContinue)) {
    Write-Host "DroidCam is not running." -ForegroundColor Yellow
    Write-Host "Activate DroidCam, confirm the phone feed, and launch the shortcut again."
    Read-Host "Press Enter to close"
    exit 2
}

$runId = "{0}-{1}-{2}" -f $RunIdPrefix, (Get-Date -Format "yyyyMMdd-HHmmss-fff"), ([guid]::NewGuid().ToString("N").Substring(0, 6))
$runnerArguments = @(
    "-m", "tools.covis_live",
    "--camera-a", $CameraA,
    "--camera-a-name", $CameraAName,
    "--camera-a-backend", $CameraABackend,
    "--camera-b", $CameraB,
    "--camera-b-name", $CameraBName,
    "--camera-b-backend", $CameraBBackend,
    "--width", $Width.ToString(),
    "--height", $Height.ToString(),
    "--fps", $Fps.ToString(),
    "--release-timeout", $ReleaseTimeout.ToString(),
    "--run-id", $runId,
    "--require-cycles", "0"
)

if ($RecordVideo) {
    $runnerArguments += @(
        "--record-video",
        "--video-codec", "MJPG",
        "--video-fps", $Fps.ToString()
    )
}

if (-not $FeatureOnly) {
    $runnerArguments += @(
        "--weights", $WeightsPath,
        "--expected-model-sha256", $expectedModelSha256,
        "--confidence", $Confidence.ToString([System.Globalization.CultureInfo]::InvariantCulture)
    )
}

Write-Host "Starting VeriSwarm three-panel camera demo" -ForegroundColor Cyan
Write-Host "Run ID: $runId"
Write-Host "Controls: s = save screenshot, q = quit and release cameras"
if ($FeatureOnly) {
    Write-Host "Mode: feature-only overlap (YOLO boxes disabled)." -ForegroundColor Yellow
}
else {
    Write-Host "Mode: semantic YOLO with class, confidence and projected intersections." -ForegroundColor Green
    Write-Host "Model SHA-256: $actualModelSha256"
}

Push-Location $codebase
try {
    & $PythonPath @runnerArguments
    $runnerExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($runnerExitCode -ne 0) {
    Write-Host "Demo exited with code $runnerExitCode. Evidence was preserved under run ID $runId." -ForegroundColor Red
    Read-Host "Press Enter to close"
}

exit $runnerExitCode

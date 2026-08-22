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

function Get-DirectShowVideoDeviceNames {
    if (-not ("VeriSwarm.DirectShow.VideoDevices" -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

namespace VeriSwarm.DirectShow {
    [ComImport, Guid("62BE5D10-60EB-11D0-BD3B-00A0C911CE86")]
    internal class SystemDeviceEnum { }

    [ComImport, Guid("29840822-5B84-11D0-BD3B-00A0C911CE86"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface ICreateDevEnum {
        [PreserveSig]
        int CreateClassEnumerator([In] ref Guid type, out IEnumMoniker enumerator, int flags);
    }

    [ComImport, Guid("55272A00-42CB-11CE-8135-00AA004BB851"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IPropertyBag {
        [PreserveSig]
        int Read([MarshalAs(UnmanagedType.LPWStr)] string name, [MarshalAs(UnmanagedType.Struct)] out object value, IntPtr errorLog);

        [PreserveSig]
        int Write([MarshalAs(UnmanagedType.LPWStr)] string name, [In, MarshalAs(UnmanagedType.Struct)] ref object value);
    }

    public static class VideoDevices {
        private static readonly Guid VideoInputDeviceCategory = new Guid("860BB310-5D01-11D0-BD3B-00A0C911CE86");
        private static readonly Guid PropertyBagId = new Guid("55272A00-42CB-11CE-8135-00AA004BB851");

        public static string[] Names() {
            var names = new List<string>();
            object deviceEnumeratorObject = null;
            IEnumMoniker enumerator = null;
            try {
                deviceEnumeratorObject = new SystemDeviceEnum();
                var deviceEnumerator = (ICreateDevEnum)deviceEnumeratorObject;
                Guid category = VideoInputDeviceCategory;
                int result = deviceEnumerator.CreateClassEnumerator(ref category, out enumerator, 0);
                if (result != 0 || enumerator == null) return names.ToArray();

                var monikers = new IMoniker[1];
                IntPtr fetched = Marshal.AllocCoTaskMem(sizeof(int));
                try {
                    while (enumerator.Next(1, monikers, fetched) == 0) {
                        IMoniker moniker = monikers[0];
                        object bagObject = null;
                        try {
                            Guid bagId = PropertyBagId;
                            moniker.BindToStorage(null, null, ref bagId, out bagObject);
                            var bag = (IPropertyBag)bagObject;
                            object value;
                            if (bag.Read("FriendlyName", out value, IntPtr.Zero) == 0 && value != null) {
                                names.Add(value.ToString());
                            }
                        }
                        finally {
                            if (bagObject != null && Marshal.IsComObject(bagObject)) Marshal.ReleaseComObject(bagObject);
                            if (moniker != null && Marshal.IsComObject(moniker)) Marshal.ReleaseComObject(moniker);
                        }
                    }
                }
                finally {
                    Marshal.FreeCoTaskMem(fetched);
                }
            }
            finally {
                if (enumerator != null && Marshal.IsComObject(enumerator)) Marshal.ReleaseComObject(enumerator);
                if (deviceEnumeratorObject != null && Marshal.IsComObject(deviceEnumeratorObject)) Marshal.ReleaseComObject(deviceEnumeratorObject);
            }
            return names.ToArray();
        }
    }
}
'@
    }

    return @([VeriSwarm.DirectShow.VideoDevices]::Names())
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
$allowDegradedStartup = $config.allow_degraded_startup -eq $true

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
    $expectedDeviceName = if ($null -eq $camera.expected_device_name) {
        $null
    }
    else {
        [string]$camera.expected_device_name
    }
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
    if ($null -ne $expectedDeviceName) {
        if ([string]::IsNullOrWhiteSpace($expectedDeviceName)) {
            throw "Camera $name has an empty expected_device_name."
        }
        if ($backend -ne "dshow" -or $source -notmatch '^\d+$') {
            throw "Camera $name may use expected_device_name only with a numeric DirectShow source."
        }
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
        ExpectedDeviceName = $expectedDeviceName
        ResolvedDeviceName = $null
        Node = if ($null -eq $camera.node) { $null } else { [string]$camera.node }
        CalibrationId = if ($null -eq $camera.calibration_id) { $null } else { [string]$camera.calibration_id }
    }
}

$deviceBoundSpecs = @($cameraSpecs | Where-Object {
    -not [string]::IsNullOrWhiteSpace($_.ExpectedDeviceName)
})
if ($deviceBoundSpecs.Count -gt 0) {
    if ($env:OS -ne "Windows_NT") {
        throw "expected_device_name requires Windows DirectShow enumeration."
    }
    $directShowNames = @(Get-DirectShowVideoDeviceNames)
    if ($directShowNames.Count -eq 0) {
        throw "No Windows DirectShow video devices were enumerated."
    }

    $claimedDeviceNames = @{}
    foreach ($camera in $deviceBoundSpecs) {
        $expectedName = [string]$camera.ExpectedDeviceName
        $matches = @()
        for ($index = 0; $index -lt $directShowNames.Count; $index++) {
            if ($directShowNames[$index] -ceq $expectedName) {
                $matches += $index
            }
        }
        if ($matches.Count -eq 0) {
            if (-not $allowDegradedStartup) {
                throw "Camera $($camera.Name) requires DirectShow device '$expectedName', but it is disconnected or unavailable. Enumerated devices: $($directShowNames -join ', ')"
            }
            $camera.Source = "__offline_camera_$($camera.Name)__"
            Write-Warning "Camera $($camera.Name) is disconnected; retaining an explicit offline tile."
            continue
        }
        if ($matches.Count -ne 1) {
            throw "Camera $($camera.Name) expected one DirectShow device named '$expectedName', but found $($matches.Count)."
        }
        if ($claimedDeviceNames.ContainsKey($expectedName)) {
            throw "DirectShow device '$expectedName' is assigned to more than one camera."
        }
        $claimedDeviceNames[$expectedName] = $true
        $camera.Source = [string]$matches[0]
        $camera.ResolvedDeviceName = $expectedName
    }
}

$width = [int](Get-NumberSetting $config "width" 640 1)
$height = [int](Get-NumberSetting $config "height" 360 1)
$fps = Get-NumberSetting $config "fps" 15 0.01
$captureFourcc = if ($null -eq $config.capture_fourcc) { $null } else { [string]$config.capture_fourcc }
if ($null -ne $captureFourcc -and ($captureFourcc.Length -ne 4 -or $captureFourcc -notmatch '^[\x20-\x7E]{4}$')) {
    throw "capture_fourcc must contain exactly four printable ASCII characters."
}
$analysisFps = Get-NumberSetting $config "analysis_fps" 3 0.01
$appearanceThreshold = Get-NumberSetting $config "appearance_threshold" 0.60 0
if ($appearanceThreshold -gt 1) {
    throw "Configuration value appearance_threshold must not exceed 1."
}
$personColourThreshold = Get-NumberSetting $config "person_colour_threshold" 0.60 0
if ($personColourThreshold -gt 1) {
    throw "Configuration value person_colour_threshold must not exceed 1."
}
$confidence = Get-NumberSetting $config "confidence" 0.25 0.01
if ($confidence -gt 1) {
    throw "Configuration value confidence must not exceed 1."
}
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
$dashboardBind = if ($null -eq $config.dashboard_bind) { "127.0.0.1" } else { [string]$config.dashboard_bind }
$dashboardPort = [int](Get-NumberSetting $config "dashboard_port" 8780 1)
if ($dashboardPort -gt 65535) {
    throw "dashboard_port must not exceed 65535."
}
if ($dashboardBind -notin @("127.0.0.1", "localhost", "::1") -and [string]::IsNullOrWhiteSpace($env:VERISWARM_MULTICAM_TOKEN)) {
    throw "A non-loopback dashboard_bind requires VERISWARM_MULTICAM_TOKEN."
}
$rescueEnabled = $null -ne $config.rescue -and $config.rescue.enabled -eq $true
if ($rescueEnabled) {
    foreach ($property in @("mission_id", "endpoint", "outbox_path", "model_id")) {
        if ([string]::IsNullOrWhiteSpace([string]$config.rescue.$property)) {
            throw "rescue.$property is required when rescue integration is enabled."
        }
    }
    $rescuePublishHz = Get-NumberSetting $config.rescue "publish_hz" 2 0.01
    if ($rescuePublishHz -gt 3) {
        throw "rescue.publish_hz must not exceed 3."
    }
    foreach ($camera in $cameraSpecs) {
        if ([string]::IsNullOrWhiteSpace($camera.Node)) {
            throw "Camera $($camera.Name) requires node when rescue integration is enabled."
        }
    }
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
    Write-Host "Appearance assumption threshold: $(Convert-Invariant $appearanceThreshold)"
    Write-Host "Person torso colour threshold: $(Convert-Invariant $personColourThreshold)"
    Write-Host "YOLO confidence threshold: $(Convert-Invariant $confidence)"
    Write-Host "Capture FOURCC: $(if ($null -eq $captureFourcc) { 'driver default' } else { $captureFourcc })"
    foreach ($camera in $cameraSpecs) {
        $identity = if ($null -eq $camera.ResolvedDeviceName) {
            ""
        }
        else {
            " => $($camera.ResolvedDeviceName)"
        }
        Write-Host "  $($camera.Name): $($camera.Source) [$($camera.Backend)]$identity"
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
    "--appearance-threshold", (Convert-Invariant $appearanceThreshold),
    "--person-colour-threshold", (Convert-Invariant $personColourThreshold),
    "--release-timeout", (Convert-Invariant $releaseTimeout),
    "--display-width", $displayWidth.ToString(),
    "--display-height", $displayHeight.ToString(),
    "--run-id", $runId,
    "--dashboard-bind", $dashboardBind,
    "--dashboard-port", $dashboardPort.ToString()
)
if ($null -ne $captureFourcc) {
    $runnerArguments += @("--capture-fourcc", $captureFourcc)
}
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
        "--confidence", (Convert-Invariant $confidence)
    )
}
if ($rescueEnabled) {
    $runnerArguments += @(
        "--rescue-mission-id", [string]$config.rescue.mission_id,
        "--rescue-endpoint", [string]$config.rescue.endpoint,
        "--rescue-outbox", [string]$config.rescue.outbox_path,
        "--rescue-model-id", [string]$config.rescue.model_id,
        "--rescue-publish-hz", (Convert-Invariant $rescuePublishHz)
    )
    foreach ($camera in $cameraSpecs) {
        $runnerArguments += @("--camera-node", $camera.Name, $camera.Node)
        if (-not [string]::IsNullOrWhiteSpace($camera.CalibrationId)) {
            $runnerArguments += @("--camera-calibration", $camera.Name, $camera.CalibrationId)
        }
    }
}

Write-Host "Starting VeriSwarm multi-camera dashboard" -ForegroundColor Cyan
Write-Host "Run ID: $runId"
Write-Host "Cameras: $($cameraSpecs.Count); pairs: $(($cameraSpecs.Count * ($cameraSpecs.Count - 1)) / 2)"
Write-Host "Controls: s = save dashboard, q = quit and release every source"
Write-Host "Browser bridge: http://$dashboardBind`:$dashboardPort/status and /stream.mjpg"
Write-Host "Rescue observations: $(if ($rescueEnabled) { 'enabled through dedicated durable outbox' } else { 'disabled' })"
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

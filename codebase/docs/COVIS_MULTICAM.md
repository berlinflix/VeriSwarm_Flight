# Experimental VeriSwarm multi-camera dashboard

## Scope and plan boundary

`tools.covis_multicam` is an experimental 2–5 source visualizer. It does not
replace the frozen `tools.covis_live` two-camera internal-qualifier command,
Samik's reviewed semantic shortcut, or Suyash's current GO/NO-GO contract.
Use a new run ID and retain its evidence separately. Suyash must approve any
future use as qualification evidence.

The runner is unarmed and has no flight, signing, OP-TEE or actuator interface.

## What it displays

- A compact top strip shows every live source with the exact class/confidence
  boxes from that source's one YOLO invocation.
- The larger centre grid contains every unordered pair. Two, three, four and
  five sources therefore produce 1, 3, 6 and 10 pair tiles respectively.
- Each valid tile projects the first camera into the second camera's 2-D image
  plane using that pair's current ORB/RANSAC homography. It uses the reviewed
  magenta/blue/green/cyan/yellow geometry and displays `AGREE`, `DISPUTE`,
  `ABSTAIN` or feature-only `COVISIBLE`.
- A pair with no accepted homography-projected intersection displays
  `ABSTAIN / NO VALID INTERSECTION` and its exact reason. If no pair intersects,
  the dashboard says `NO CAMERA PAIR HAS A VALID INTERSECTION`.
- In semantic mode, an `ABSTAIN` pair may additionally show an explicitly
  labelled `ASSUMED SAME OBJECT` side-by-side panel when same-class YOLO crops
  exceed the configured rough colour/shape appearance threshold. This is an
  operator-requested visualization heuristic: it is recorded as
  `appearance_assumption`, keeps the geometric decision at `ABSTAIN`, and always
  states `identity_proven=false`. It must not be reported as calibrated geometry,
  object re-identification or proof that two similar instances are one object.

Every pair is directional only for projection: `cam1 -> cam2` means Camera 1's
footprint was projected into Camera 2 coordinates. It is still the one unordered
pair `{cam1, cam2}` and is not counted twice. These are independent 2-D planar
homographies, not calibrated stereo, multi-view 3-D reconstruction or 3-D IoU.

## Important three-phone constraint

Windows must expose four independent OpenCV sources. A single DroidCam virtual
camera cannot carry three independently addressable phones. Use one of:

1. three distinct local phone URLs, preferably one MJPEG/RTSP stream per phone;
2. three genuinely distinct virtual camera devices with separate OpenCV indexes;
3. a tested mixture of unique URLs and indexes.

Do not configure the same URL or device index twice; the runner rejects duplicate
sources. Keep phone streams on Samik's local network. Do not use Internet relays.
Test 640×360 at 10–15 FPS first. Five YOLO inferences and ten RANSAC pair checks
are substantially heavier than the reviewed two-camera run, so analysis defaults
to 3 FPS while each capture worker continuously keeps only its newest frame.

## Windows P2 example: one webcam and three phones

Map the USB webcam and verify each phone URL separately before the combined run.
From `codebase`:

```powershell
python -m tools.covis_multicam `
  --camera cam1 0 dshow `
  --camera cam2 "http://PHONE_1_IP:PORT/VIDEO_PATH" ffmpeg `
  --camera cam3 "http://PHONE_2_IP:PORT/VIDEO_PATH" ffmpeg `
  --camera cam4 "http://PHONE_3_IP:PORT/VIDEO_PATH" ffmpeg `
  --width 640 --height 360 --fps 15 `
  --analysis-fps 3 `
  --release-timeout 20 `
  --weights yolov8n.pt `
  --expected-model-sha256 f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36 `
  --run-id MULTICAM-P2-01 `
  --record-video --video-codec MJPG --video-fps 3
```

Replace every placeholder privately. Never commit phone URLs, credentials,
weights or generated evidence. For feature-only setup, omit both model options;
the decisions will be `COVISIBLE` or `ABSTAIN`, and no YOLO boxes will appear.

For distinct virtual devices, use each verified index/backend instead:

```powershell
--camera cam2 2 msmf --camera cam3 3 dshow --camera cam4 4 dshow
```

## Portable variable-camera shortcut

The GitHub handoff includes a JSON-driven Windows launcher and shortcut installer.
Copy the tracked example to a local ignored configuration and replace every
placeholder with a genuinely different source:

```powershell
Copy-Item `
  ".\config\covis_multicam.example.json" `
  ".\config\covis_multicam.local.json"
```

The local JSON accepts 2–5 entries under `cameras`. Add or remove whole camera
objects; do not edit Python or the shortcut when the source count changes. The
launcher rejects placeholder, empty, duplicate or unsupported sources before it
opens any camera.

On Windows, numeric DirectShow indices are not stable when a device is unplugged.
For each numeric `dshow` entry, set `expected_device_name` to the exact DirectShow
friendly name. The launcher resolves the current index by that name and refuses
to start if the expected device is missing or ambiguous. This prevents a phone
camera from inheriting a disconnected USB webcam's index and appearing under the
wrong label. The `source` remains a numeric compatibility value; the resolved
index is printed during validation.

From `codebase`, validate the local configuration and frozen semantic runtime:

```powershell
$python = "C:\path\to\approved-semantic-venv\Scripts\python.exe"
$weights = ".\yolov8n.pt"
$config = ".\config\covis_multicam.local.json"

powershell -NoProfile -ExecutionPolicy Bypass -File `
  ".\tools\install_covis_multicam_shortcut.ps1" `
  -CameraConfigPath $config `
  -PythonPath $python `
  -WeightsPath $weights `
  -ValidateOnly
```

If validation passes, repeat the same command without `-ValidateOnly`. It creates
`VeriSwarm Multi-Camera Demo.lnk` on that laptop's Desktop. The `.lnk` stores only
that laptop's resolved script, Python, model and local-config paths. Each launch
reads the current local JSON and creates a unique run ID, so changing from two to
three or four cameras requires only a validated JSON edit. Use `-FeatureOnly` on
both installer and launcher only when intentionally omitting YOLO. The generated
shortcut is an experimental zero-acceptance-cycle dashboard and does not authorize
qualification evidence.

## Operation and evidence

- `s` saves the exact dashboard under `screenshots/`.
- `q` exits, finalizes the optional dashboard video, stops all workers together,
  and reopens/reads every source to verify release.
- Ctrl+C follows the same bounded cleanup path.
- `events.jsonl` records every source observation and every pair's health, skew,
  RANSAC result, projected footprint/intersection geometry, projected boxes,
  matching box intersection and displayed decision.
- `summary.json` records source shutdown/reopen proof and video metadata.
- `video/multicam_dashboard.avi` contains the complete displayed dashboard when
  `--record-video` is enabled.
- A requested video that cannot open or finalize fails the run.

Use `--no-release-probe` only for a disposable diagnostic when a phone server
cannot accept a quick reconnect. It is not release proof.

## Physical setup

Place all cameras in landscape orientation, powered and rigidly mounted. Every
pair you expect to intersect needs shared textured background, not just the same
small object. Add cameras one at a time and verify distinct views. Start with two,
then three, then four; this identifies the source or pair that causes latency,
blur, skew or weak RANSAC evidence.

No-intersection is a valid `ABSTAIN`, not a software failure and never an
`AGREE`. `DISPUTE` is meaningful only when that pair remains co-visible and the
two measured semantic claims differ.

The optional rough-appearance heuristic defaults to `--appearance-threshold
0.60`. It compares only same-class YOLO crops using HSV colour distribution and
bounding-box aspect similarity. The dashboard counts these separately as
`assumed object intersections`; it never adds them to `valid intersections`.

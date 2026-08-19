# VeriSwarm live co-visibility setup: USB webcam + Android DroidCam

## Ownership and safety boundary

- **Ayush owns camera-software design and focused development tests.** Ayush has
  no runtime cable, IP address or on-stage terminal in the five-cable qualifier.
- **Samik is the technical owner, reviewer and live operator:** Windows P2
  environment, camera mapping, pinned model, thresholds, clean/attack/recovery
  labels, evidence, release proof and the camera-to-Bravo handover.
- **Abhijan supplies and applies the controlled physical attack artifact.**
- **Suyash owns final GO/NO-GO and evidence acceptance, not camera operation.**

`covis_live` is unarmed. It has no flight, actuator, receipt-signing or OP-TEE
interface. The webcam process must exit and release both sources before Bravo
starts on the same P2 host. The later Alpha/OP-TEE preflight runs on the Jetson.

## What the result means

ORB/RANSAC answers only whether both frames share enough scene structure to be
compared. It does not detect an adversarial patch. A semantic `AGREE` or
`DISPUTE` is emitted only when:

1. both cameras are healthy;
2. the frames are within the frozen skew bound;
3. the existing `protocol/covis_features.py` gate confirms co-visibility; and
4. the same pinned YOLO model produces measured `PerceptionClaim`s for both.

Camera loss, stale/skewed frames, blur, poor exposure, inadequate scene overlap
or detector failure produces `ABSTAIN`. Feature-only mode reports `COVISIBLE`,
never semantic agreement. With heterogeneous USB/DroidCam inputs, the tool
measures **host receive-time skew**, not hardware exposure-time synchronization.
Do not describe the feeds as hardware-synchronized or calibrated stereo.

The display/evidence also reports two RANSAC-projected IoU measurements:

- `view_IoU`: Camera A's image footprint projected into Camera B's image plane,
  intersected with Camera B's full frame;
- `box_IoU`: the best same-class detector-box IoU after projecting Camera A's
  box into Camera B's image plane.

These are planar-homography demo measurements, not calibrated 3-D IoU. Raw box
coordinates from the two different viewpoints are never compared. `box_IoU`
may be unavailable when either detector is empty, no same-class pair exists or
the transform is invalid; that is evidence, not a value to replace with zero.

The operator composite has three panels:

1. Camera A with its exact detector class/confidence boxes;
2. Camera B with its exact detector class/confidence boxes; and
3. Camera A geometry projected into Camera B's 2-D coordinates.

The third panel dims non-overlapping Camera B pixels, outlines projected Camera
A in magenta and Camera B in blue, and fills the valid shared-view intersection
translucent green. Projected Camera A boxes are cyan quadrilaterals; Camera B
boxes are solid green rectangles; valid same-class box intersections are yellow.
It displays the decision, `view_IoU`, intersection area, best `box_IoU`, RANSAC
inliers and host receive-time skew. It must be described as **2-D
homography-projected overlap**, never calibrated stereo or 3-D IoU.

One validated `SpatialEvidence` calculation supplies both the displayed polygons
and the JSONL metrics. NaN, infinite, degenerate, non-convex or numerically
unbounded projections are rejected without drawing repaired geometry. A finite
projection entirely outside Camera B is retained as explicit zero overlap.

In semantic mode, both live camera panels draw the exact YOLO boxes used to
construct the decision: mission-taxonomy class name, confidence and a
class-stable colour. The JSONL event retains the same normalized detections.
These are not a second visualization-only inference pass, so the screen and
decision evidence cannot silently diverge.

A clean or recovery key press advances a counted cycle only when both camera
claims contain detections. Empty-versus-empty semantic agreement is still an
honest scene result, but it cannot satisfy this physical target demonstration;
the event records `both_camera_detections_required` instead.

## 1. Physical configuration

1. Mount the Android phone in landscape orientation on the tripod. Use the rear
   camera, disable auto-rotation, enable Do Not Disturb, connect power and disable
   battery optimisation for DroidCam.
2. Rigidly mount the USB webcam. Do not hold either camera by hand during an
   accepted run.
3. Aim both at the same textured scene and supported detector-class target.
   Start with roughly 15–25 degrees of viewpoint separation. Avoid blank walls,
   glare, moving screens and repeated patterns.
4. Arrange the attack so it affects one target view without hiding the whole
   shared scene. If the scene itself stops matching, the correct result is
   `ABSTAIN`, not a semantic `DISPUTE`.
5. Label the physical devices `A — USB` and `B — ANDROID`. Never swap them after
   freezing the command.

## 2. Identify the USB webcam on Windows P2

Connect only the USB webcam first:

```powershell
Get-PnpDevice -Class Camera | Format-Table Status, FriendlyName, InstanceId
Get-PnpDevice -Class Image | Format-Table Status, FriendlyName, InstanceId
```

If one class is absent, use the other command. Record the USB camera's friendly
name and instance ID. Windows device numbers are OpenCV indexes, not the PnP
instance ID, so map the index in the frozen Python environment:

```powershell
@'
import cv2

for index in range(6):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    ok, frame = cap.read()
    backend = cap.getBackendName() if cap.isOpened() else None
    print(index, "opened=", cap.isOpened(), "read=", ok,
          "shape=", None if frame is None else frame.shape,
          "backend=", backend)
    cap.release()
'@ | python -
```

Close Windows Camera, Teams, browsers and every other camera application first.
Connect only the intended USB camera while mapping it. Record the working index,
then reconnect the Android source and confirm the two inputs show different
views. Index stability is accepted only for the frozen P2 hardware/USB-port
layout; repeat mapping after any port or device change.

## 3. Connect DroidCam

### Preferred Windows P2 route: local HTTP video

The simplest route avoids a virtual-camera dependency and opens the phone's
local DroidCam HTTP stream directly in OpenCV:

1. Connect the Android phone and Samik P2 to the same local Wi-Fi/hotspot.
2. Open DroidCam on Android and note the displayed phone IP and port (normally
   `4747`).
3. Use `http://PHONE_IP:4747/video` as Camera B.
4. Keep the stream local. Do not expose port 4747 to the internet.

Probe it using the same OpenCV environment as VeriSwarm:

```powershell
@'
import cv2

url = "http://PHONE_IP:4747/video"
cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
ok, frame = cap.read()
print("opened=", cap.isOpened(), "read=", ok,
      "shape=", None if frame is None else frame.shape,
      "backend=", cap.getBackendName() if cap.isOpened() else None)
cap.release()
raise SystemExit(0 if ok and frame is not None else 1)
'@ | python -
```

Replace `PHONE_IP` before running. Stop here if the probe fails.

### Existing working Windows virtual-camera route

If the already-installed DroidCam Windows client exposes a virtual camera, map
its separate OpenCV index with the same DirectShow probe. Use that integer as
Camera B with `--camera-b-backend dshow`. Prove the panels are distinct before
freezing. Do not install or upgrade the client during final rehearsal.

### Frozen fallback if DroidCam is unstable

There is no second USB camera. Time-box DroidCam setup to ten minutes. If its
stream drops, buffers badly or cannot meet the clean gate, keep the same Android
phone and tripod but switch Camera B to a prevalidated local MJPEG/RTSP camera
app or transport whose URL OpenCV can open. `covis_live` needs no code change:

```powershell
python -m tools.covis_live `
  --camera-a 0 --camera-a-name usb_webcam --camera-a-backend dshow `
  --camera-b "http://PHONE_IP:PORT/VIDEO_PATH" `
  --camera-b-name android_local_fallback --camera-b-backend ffmpeg `
  --release-timeout 20 `
  --run-id SETUP-PHONE-FALLBACK-01 --require-cycles 0
```

Freeze the new local URL/app/transport and start a new accepted run ID. Never
send the feed through an Internet relay, switch a camera source during a run or
reuse evidence from the discarded configuration.
If no live replacement works, use the retained recording only as an explicitly
labelled fallback; do not call it a live demonstration.

## 4. Verify the Windows P2 environment

From the directory containing `tools/`, `protocol/` and `perception/`:

```powershell
python -c "import cv2, numpy; print('OpenCV', cv2.__version__)"
python -m pytest -q tests/test_covis_features.py tests/test_covis_live.py
```

For semantic mode, inspect the frozen P2 stack before changing anything:

```powershell
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
python -c "import ultralytics; print(ultralytics.__version__)"
Get-FileHash -Algorithm SHA256 .\yolov8n.pt
```

The accepted baseline model hash is:

```text
f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36
```

Do not upgrade Torch, Ultralytics or OpenCV after the P2 environment is frozen.
If Torch or Ultralytics is missing, feature-only setup can proceed, but the
semantic attack claim cannot be demonstrated until Samik reviews the P2 stack.

### Portable Windows semantic-demo shortcut

Git carries the portable launcher and shortcut installer, never a machine-specific
`.lnk`, virtual environment or model weight. After checking out the reviewed camera
commit on another Windows laptop, privately place the approved `yolov8n.pt` under
`codebase/` and keep its required SHA-256 unchanged. From the repository root run:

```powershell
$python = "C:\path\to\approved-semantic-venv\Scripts\python.exe"
$weights = "$PWD\codebase\yolov8n.pt"

powershell -NoProfile -ExecutionPolicy Bypass -File `
  ".\codebase\tools\install_covis_semantic_shortcut.ps1" `
  -PythonPath $python `
  -WeightsPath $weights `
  -CameraA 0 -CameraABackend dshow `
  -CameraB 2 -CameraBBackend msmf
```

The installer validates the semantic imports and frozen model hash before creating
`VeriSwarm Semantic Camera Demo.lnk` on that laptop's Desktop. The generated
shortcut resolves the current checkout and local Python/model paths, generates a
fresh create-once demo run ID on every launch and never embeds model bytes. Camera
indices are per-laptop values: confirm the physical USB and DroidCam mapping before
installation. Use `-ValidateOnly` to check without creating a shortcut, and use
`-Force` only to replace the intended existing shortcut. The shortcut runs a
zero-required-cycle local demonstration; it is not acceptance evidence and does not
authorize the three-cycle qualification.

## 5. Feature-only alignment run

This proves capture, health, skew and co-visibility only. Samik's preserved P2
diagnostic mapped the Owl USB webcam to index `0`/DSHOW and the DroidCam virtual
camera to index `2`/MSMF. Reconfirm those views after any USB/device change.

```powershell
python -m tools.covis_live `
  --camera-a 0 `
  --camera-a-name usb_webcam `
  --camera-a-backend dshow `
  --camera-b 2 `
  --camera-b-name android_droidcam `
  --camera-b-backend msmf `
  --width 640 --height 360 --fps 15 `
  --release-timeout 20 `
  --record-video --video-codec MJPG --video-fps 15 `
  --run-id SETUP-OVERLAP-P2-01 `
  --require-cycles 0
```

Replace camera index `0` only if the mapping probe found a different USB index.
The display must reach `COVISIBLE`, and the `READY` line must report the expected
backends. Adjust only physical framing during this setup run. Press `q`; the
final line must report `release=True`. Retain a screenshot and the internally
recorded three-panel video as feature-only review evidence.

## 6. Freeze and run the semantic demonstration

After the clean setup is stable, freeze camera sources, placement and thresholds.
Do not tune thresholds after seeing attack results.

```powershell
python -m tools.covis_live `
  --camera-a 0 `
  --camera-a-name usb_webcam `
  --camera-a-backend dshow `
  --camera-b 2 `
  --camera-b-name android_droidcam `
  --camera-b-backend msmf `
  --width 640 --height 360 --fps 15 `
  --release-timeout 20 `
  --record-video --video-codec MJPG --video-fps 15 `
  --max-receive-skew-ms 150 `
  --m-min 15 `
  --weights yolov8n.pt `
  --expected-model-sha256 f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36 `
  --run-id IHQ-20260819-WEBCAM-P2-01 `
  --require-cycles 3
```

Controls:

- With the artifact absent and `AGREE` visible, press `c`.
- Have Abhijan apply the artifact. When `DISPUTE` or a valid co-visibility
  `ABSTAIN` is visible, press `a`.
- Remove the artifact. When `AGREE` returns, press `r`.
- Repeat three times. `s` saves an extra evidence pair; `q` exits.

A camera-loss, blur, stale-frame or skew abstention does not count as attack
evidence. Fix the rig and repeat that cycle without changing the frozen command.

## 7. Evidence and exit gate

Each run creates a new directory under `results/covis_live/<run-id>/` containing:

- `run_config.json` with sources, thresholds and model hash;
- append-only `events.jsonl` with the exact displayed detections;
- raw A/B frames plus the exact three-panel annotated composite on labels/state
  changes;
- `video/three_panel.avi`, containing the complete displayed composite; and
- `summary.json` with decision counts, completed cycles, video metadata and
  camera-release proof.

Every JSONL `projected_iou` record includes the projected Camera A footprint,
Camera B frame polygon, shared-view intersection polygon and areas, projected
Camera A boxes, Camera B boxes, every valid same-class box intersection and the
displayed IoUs. The display does not recompute these values.

The process refuses to overwrite a run directory. An accepted semantic run exits
zero only when the required cycles pass and both sources close and reopen.
`run_config.json` records requested backends. `summary.json` records requested
and actual OpenCV backends plus a same-backend close/reopen probe for both
sources.

Accepted cycle runs require `--record-video`. The codec must be a four-character
OpenCV code and defaults to `MJPG`; recording FPS defaults to capture FPS. The
writer must open, produce at least one frame, release, create a non-empty file
and pass a decode probe. `summary.json.video_recording` records path, SHA-256,
codec, resolution, frames, FPS, duration and bytes. The writer is finalized
before either camera worker is stopped and before release probes begin. A writer
open/finalization failure fails the run. External screen recording is backup
evidence only.

Windows MSMF capture can take roughly ten seconds to return from a blocked read
after release. The runner therefore signals both workers first and gives them one
shared, bounded 20-second shutdown budget. Keep `--release-timeout 20` in the
frozen P2 command. `summary.json.worker_shutdown` records each worker's closed
state, observed elapsed time, timeout and error. Do not raise the bound during an
accepted run; a worker still alive after the bound makes `release_verified=false`.

After `COMPLETE ... release=True`:

1. close DroidCam on the phone/client;
2. confirm no `covis_live` process remains;
3. open and close the USB webcam once with the frozen DirectShow probe if Samik
   wants an additional manual release witness;
4. start Bravo on P2 only after camera release is proved;
5. synchronize clocks and disconnect unrelated Wi-Fi after the camera stage;
6. Suyash then runs the fresh Jetson OP-TEE preflight before Alpha starts.

## 8. Troubleshooting without weakening the gate

- **DroidCam delay/skew:** lower the phone stream resolution/FPS before the
  frozen run, keep the phone powered, and use a local hotspot. Do not silently
  enlarge the skew threshold during evidence capture.
- **No co-visibility:** add textured, non-repeating background structure and
  reduce camera separation. Do not lower `m_min` during an accepted run.
- **Blur/exposure abstention:** clean lenses, lock the tripod, improve lighting
  and let autofocus settle before pressing a label key.
- **YOLO disagreement in clean state:** verify both views contain the same
  supported class and that the model hash is correct. Do not call a clean
  disagreement an attack success.
- **No boxes on either panel:** feature-only mode intentionally has no detector
  boxes. For boxes and class highlighting on both cameras, use semantic mode
  with the pinned weights. Stop if either clean view cannot detect the supported
  target; the exact boxes used for each claim must be visible on its panel.
- **Slow MSMF shutdown:** retain `--release-timeout 20`. Both workers receive
  their release signal before the shared timer starts waiting. Preserve the run
  and report the `worker_shutdown` evidence if either worker exceeds the bound.
- **Video writer fails:** preserve the failed run ID and its summary. Verify the
  frozen codec is supported by P2; do not disable internal recording for an
  accepted run or substitute external screen capture.
- **GUI unavailable:** run feature-only diagnostics with `--headless
  --duration-seconds N --require-cycles 0`; the panel semantic cycle requires a
  display and explicit operator labels.
- **Release probe fails:** do not start Bravo. Stop DroidCam normally, close
  Windows camera applications, confirm `covis_live` is gone in Task Manager,
  rerun the single-source probes, and begin a new run ID after correction.

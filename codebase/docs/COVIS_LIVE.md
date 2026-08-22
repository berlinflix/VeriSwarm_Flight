# VeriSwarm live co-visibility setup: USB webcam + Android DroidCam

## Ownership and safety boundary

- **Ayush is the primary rig operator:** phone, tripod, USB webcam, camera IDs,
  framing, DroidCam connection, clean/attack/recovery labels and evidence copy.
- **Samik is the technical owner and reviewer:** Jetson environment, pinned model,
  `tools/covis_live.py`, thresholds, failure handling, release proof and the
  webcam-to-OP-TEE handover. Samik oversees Ayush and is the recovery operator.
- **Abhijan supplies and applies the controlled physical attack artifact.**
- **Suyash owns final GO/NO-GO and evidence acceptance, not camera operation.**

`covis_live` is unarmed. It has no flight, actuator, receipt-signing or OP-TEE
interface. The webcam process must exit and release both sources before Alpha or
the OP-TEE stage begins.

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

In semantic mode, both live camera panels draw the exact YOLO boxes used to
construct the decision: mission-taxonomy class name, confidence and a
class-stable colour. The JSONL event retains the same normalized detections.
These are not a second visualization-only inference pass, so the screen and
decision evidence cannot silently diverge.

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

## 2. Identify the USB webcam on Jetson

Connect only the USB webcam first:

```bash
ls -l /dev/video*
ls -l /dev/v4l/by-id/ 2>/dev/null || true
v4l2-ctl --list-devices
```

Prefer the stable `/dev/v4l/by-id/...` symlink when present. If `v4l2-ctl` is not
installed, `ls` is sufficient for the first probe; do not change packages during
the final rehearsal.

Check that nothing owns the selected device:

```bash
fuser -v /dev/video0
```

An empty result is expected. Do not kill an unknown process; stop it through its
normal application first.

## 3. Connect DroidCam

### Preferred Jetson route: local HTTP video

The current official DroidCam Linux desktop package is distributed for x86-64;
Jetson is ARM64. Do not install an AMD64 package on Jetson. The simplest route is
to use the DroidCam app's local video URL during the webcam stage:

1. Connect the Android phone and Jetson to the same local Wi-Fi/hotspot.
2. Open DroidCam on Android and note the displayed phone IP and port (normally
   `4747`).
3. Use `http://PHONE_IP:4747/video` as Camera B.
4. Keep the stream local. Do not expose port 4747 to the internet.

Probe it using the same OpenCV environment as VeriSwarm:

```bash
python - <<'PY'
import cv2

url = "http://PHONE_IP:4747/video"
cap = cv2.VideoCapture(url)
ok, frame = cap.read()
print("opened=", cap.isOpened(), "read=", ok,
      "shape=", None if frame is None else frame.shape)
cap.release()
raise SystemExit(0 if ok and frame is not None else 1)
PY
```

Replace `PHONE_IP` before running. Stop here if the probe fails.

### Existing working virtual-device route

If the already-installed DroidCam client successfully exposes the phone as a
Jetson V4L2 device, identify that device after starting the phone stream:

```bash
v4l2-ctl --list-devices
ls -l /dev/video*
```

Use that distinct `/dev/videoN` path as Camera B. Do not reinstall DroidCam or
`v4l2loopback` immediately before the demo. For Android USB discovery, official
DroidCam guidance requires USB debugging, a data-capable cable and `adb` on
Linux. Confirm the phone's USB-debugging prompt rather than bypassing it.

### Frozen fallback if DroidCam is unstable

There is no second USB camera. Time-box DroidCam setup to ten minutes. If its
stream drops, buffers badly or cannot meet the clean gate, keep the same Android
phone and tripod but switch Camera B to a prevalidated local MJPEG/RTSP camera
app or transport whose URL OpenCV can open. `covis_live` needs no code change:

```bash
python -m tools.covis_live \
  --camera-a /dev/video0 --camera-a-name usb_webcam \
  --camera-b "http://PHONE_IP:PORT/VIDEO_PATH" \
  --camera-b-name android_local_fallback \
  --run-id SETUP-PHONE-FALLBACK-01 --require-cycles 0
```

Freeze the new local URL/app/transport and start a new accepted run ID. Never
send the feed through an Internet relay, switch a camera source during a run or
reuse evidence from the discarded configuration.
If no live replacement works, use the retained recording only as an explicitly
labelled fallback; do not call it a live demonstration.

## 4. Verify the Jetson environment

From the directory containing `tools/`, `protocol/` and `perception/`:

```bash
python -c "import cv2, numpy; print('OpenCV', cv2.__version__)"
python -m pytest -q tests/test_covis_features.py tests/test_covis_live.py
```

For semantic mode, inspect the existing Jetson-compatible stack before changing
anything:

```bash
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
python -c "import ultralytics; print(ultralytics.__version__)"
sha256sum yolov8n.pt
```

The accepted baseline model hash is:

```text
f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36
```

Do not install generic desktop PyTorch on Jetson. If Torch or Ultralytics is
missing, feature-only setup can proceed, but the semantic attack claim cannot be
demonstrated until a JetPack-compatible stack is reviewed.

## 5. Feature-only alignment run

This proves capture, health, skew and co-visibility only:

```bash
python -m tools.covis_live \
  --camera-a /dev/video0 \
  --camera-a-name usb_webcam \
  --camera-b "http://PHONE_IP:4747/video" \
  --camera-b-name android_droidcam \
  --width 640 --height 360 --fps 15 \
  --run-id SETUP-COVIS-01 \
  --require-cycles 0
```

The display must reach `COVISIBLE`. Adjust only the physical framing during this
setup run. Press `q`; the final line must report `release=True`.

## 6. Freeze and run the semantic demonstration

After the clean setup is stable, freeze camera sources, placement and thresholds.
Do not tune thresholds after seeing attack results.

```bash
python -m tools.covis_live \
  --camera-a /dev/video0 \
  --camera-a-name usb_webcam \
  --camera-b "http://PHONE_IP:4747/video" \
  --camera-b-name android_droidcam \
  --width 640 --height 360 --fps 15 \
  --max-receive-skew-ms 150 \
  --m-min 15 \
  --weights yolov8n.pt \
  --expected-model-sha256 f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36 \
  --run-id IHQ-20260819-WEBCAM-01 \
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
- raw A/B frames plus the live-box annotated combined frame on labels/state changes;
- `summary.json` with decision counts, completed cycles and camera-release proof.

The process refuses to overwrite a run directory. An accepted semantic run exits
zero only when the required cycles pass and both sources close and reopen.

After `COMPLETE ... release=True`:

1. close DroidCam on the phone/client;
2. confirm no `covis_live` process remains;
3. if Camera B used Wi-Fi, disconnect that temporary webcam network path;
4. run a fresh OP-TEE preflight with a new evidence filename; and
5. only then start Alpha locally on Jetson.

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
- **GUI unavailable:** run feature-only diagnostics with `--headless
  --duration-seconds N --require-cycles 0`; the panel semantic cycle requires a
  display and explicit operator labels.
- **Release probe fails:** do not start OP-TEE/Alpha. Stop DroidCam normally,
  inspect device owners with `fuser -v`, and begin a new run ID after correction.

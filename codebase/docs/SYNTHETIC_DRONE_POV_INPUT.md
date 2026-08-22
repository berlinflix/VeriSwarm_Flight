# Synthetic drone-POV input lane

This lane lets VeriSwarm ingest a cinematic disaster-response video as if it were an RGB
camera feed. It complements, but does not replace, CoSys flight simulation or physical
camera tests.

## What this proves

The demonstration may prove that the exact local detector and rescue-event path can:

1. decode a continuous drone-view video without cloud access;
2. detect visible survivor candidates frame by frame;
3. retain a candidate through brief occlusion using tracking;
4. send prioritized observations to the rescue dashboard; and
5. run on an RTX laptop or, after TensorRT deployment, a Jetson Orin Nano.

It does not prove real flight, metric depth, GPS-denied localization, thermal detection,
or generalization to real disasters. Generated frames are labelled `synthetic_rgb` in
reports and are scored separately from real-aerial held-out data.

## Runtime architecture

```text
original generated MP4 (native frame rate, preferably 24 FPS)
        |
        +--> 24 FPS presentation/decode lane --> annotated dashboard/video
        |
        +--> newest-frame buffer --> detector at sustainable rate
                                   --> tracker
                                   --> person_candidate event
                                   --> durable offline outbox/dashboard
```

Display rate and inference rate are intentionally separate. The presentation may remain
24 FPS while a Nano performs inference at 5--10 FPS. The detector always receives the
newest available frame; stale frames are dropped rather than queued. The latest fresh
track/box may be rendered between inference frames with its age visible. This is an honest
real-time system and avoids latency growing throughout the clip.

`node.frame_source.VideoFileSource` is the repository adapter for MP4/MOV/AVI input. It
preserves frame index and source-media timestamp, supports deterministic replay, and never
invents pose, depth, or geolocation.

Before a run, inspect the file instead of assuming it is 24 FPS:

```bash
ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,width,height,avg_frame_rate,r_frame_rate,nb_frames \
  -of json disaster-pov.mp4
```

Do not convert a low-frame-rate clip to 24 FPS and then count duplicated frames as 24
independent inferences.

## NVIDIA training and deployment boundary

Train the portable PyTorch model on the cloud RTX GPU with CUDA mixed precision. There is
no accuracy or compatibility benefit from training on the slower Jetson itself. The
Jetson advantage is deployment: its Ampere CUDA and Tensor Cores are used through a
target-built TensorRT engine.

```text
cloud RTX 5090 + CUDA/AMP training
        -> validated .pt checkpoint
        -> static batch-1 ONNX
        -> TensorRT FP16 engine built on the actual Orin Nano
        -> Nano accuracy-equivalence and sustained camera-to-event benchmark
```

Never copy an RTX 5090 TensorRT engine to the Jetson. TensorRT plans are constrained by
platform, TensorRT version and GPU compatibility, and JetPack does not support TensorRT's
hardware-compatibility mode. Transfer `.pt` or ONNX and build the `.engine` on the target
Jetson. FP16 is the first release candidate. INT8 is optional only after representative
target calibration and the accuracy-retention gate in `docs/JETSON_ORIN_NANO_MODEL_GATE.md`.

## Clip design

Create at least three short, single-take, landscape clips with no cuts, text, bounding
boxes, HUD or baked-in thermal palette:

1. **Collapsed-building search:** slow stabilized forward flight, fixed altitude and
   downward-oblique camera; one partially occluded stationary survivor becomes visible
   through a broken wall opening.
2. **Flood rooftop:** slow lateral pass; one survivor on a roof with debris and moving
   water, plus a deliberate interval of partial occlusion.
3. **Hazard plus survivor:** survivor near smoke/fire or a blocked route, with enough
   spatial separation to show two independent alerts.

Keep people anatomically consistent, avoid crowds and rapid rotations, and ask for stable
lighting and low motion blur. Generate several candidates and reject clips with duplicated,
morphing or disappearing people before running the detector.

Generated alternate camera angles are not automatically a calibrated multi-camera scene.
Use the physical rig or CoSys camera intrinsics/extrinsics for metric multi-view fusion.
A generated second angle can show angle-dependent recall, but it cannot support NED
triangulation unless camera calibration and scene geometry are independently known.

For an adversarial-patch demonstration, add the exact frozen patch after generation with
deterministic planar tracking. Do not ask the video generator to reproduce a pixel-exact
patch across frames; generative drift would make the test undefined.

## Evaluation policy

- Do not train and report evaluation on the same generated clip.
- Keep generated-video results separate from real-aerial held-out results.
- Manually review survivor presence and occlusion intervals; a generative prompt is not a
  ground-truth annotation.
- Record source timestamp, frame index, inference timestamp, detection/track confidence,
  detector artifact ID and event delivery result.
- A single positive survivor view produces a prioritized candidate. A negative view never
  erases it; the policy in `docs/MULTIVIEW_SURVIVOR_FUSION.md` still applies.
- CoSys remains authoritative for flight, collision, NED pose, depth and mission success.

## Ownership

- **Suyash:** select and label the final clips; keep the spoken claim boundary honest.
- **Samik:** integrate `VideoFileSource` into the rescue detector runner, implement the
  newest-frame buffer, emit timestamped `person_candidate` events and benchmark both RTX
  and target TensorRT paths.
- **Ayush:** help Samik with the 24 FPS annotated presentation and frame-age overlay.
- **Pratik:** keep CoSys as the authoritative flight/metric scenario and synchronize the
  cinematic clip beat with the A-to-B/search mission story.
- **Abhijan:** review/annotate the synthetic clips, define survivor/hazard intervals and
  connect events to the dashboard without treating generated imagery as physical truth.

## Demo wording

Use: "This is an AI-generated disaster POV used as an offline RGB camera source. The same
local inference and event path runs on recorded or live frames. CoSys separately proves
vehicle motion and metric safety; the physical rig proves multi-angle co-visibility."

Do not call the generated clip real-world validation or thermal evidence.

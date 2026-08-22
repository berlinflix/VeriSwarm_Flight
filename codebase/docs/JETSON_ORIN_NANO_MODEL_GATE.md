# Jetson Orin Nano rescue-model deployment gate

This is the minimum hardware acceptance gate for the SIH 26177 person detector. It does
not ask for the fastest possible model. The selected model is the most accurate candidate
that passes this gate on the Jetson Orin Nano 8GB.

Passing a cloud GPU test, loading a PyTorch checkpoint, or showing one successful frame is
not a Jetson deployment result.

## Claim boundary

The reference target is:

- Jetson Orin Nano 8GB developer kit;
- pinned JetPack/L4T, CUDA, cuDNN and TensorRT versions;
- active cooling;
- 15 W power mode, not MAXN SUPER;
- batch size 1;
- one onboard RGB inference stream plus preprocessing, NMS/tracking, rescue-event
  creation, outbox enqueue and model/receipt bookkeeping;
- no cloud or network dependency after startup.

The physical five-camera overlap display is a separate ground demonstration. It is not
part of the one-drone Nano gate unless all five streams are separately benchmarked on the
Nano.

NVIDIA lists the Orin Nano family at 7–25 W and up to 67 TOPS. Super modes require a
suitable software image, power supply and thermal design. We deliberately qualify at
15 W so the result does not depend on MAXN SUPER.

## Current project state

As of Samik's status commit `db73384`, no cloud job has started, no trained rescue weight
exists, and no Jetson accuracy or latency result exists. Therefore the project must not
yet claim that its new rescue detector is optimized for or qualified on Orin Nano.

The existing COCO `yolov8n.pt` proves only the old protocol/demo path. It is not the new
trained aerial-survivor model.

## Candidate policy

Run the following candidates in order:

1. `YOLOv8n`, 640 input: mandatory deployment and regression baseline.
2. `YOLOv8n`, 960 input: mandatory small-person accuracy candidate.
3. `YOLOv8s`, 640 input: optional accuracy comparison if the first two complete in time.

Do not choose the fastest result automatically. Choose the highest held-out survivor
accuracy among candidates that pass the hardware gate.

The output class remains only `person_candidate`. RGB/thermal fusion and hazard classes
are separate capabilities and must not be claimed from this weight.

## Required artifact chain

For every candidate retain lineage, not raw datasets, in Git:

```text
cloud training checkpoint (.pt)
  -> static batch-1 ONNX export
  -> TensorRT FP16 engine built on the target Jetson
  -> accuracy report on the held-out set
  -> sustained Nano benchmark report
```

The TensorRT FP16 engine is the required deployed artifact. Build it on the target Jetson
with the exact selected input shape. TensorRT engines are tied to their build/runtime and
hardware context; transfer the checkpoint/ONNX and rebuild for a different Jetson rather
than assuming the Nano engine is universally portable.

INT8 is optional. It may be selected only when it is calibrated on representative aerial
disaster images on the target deployment stack and passes the accuracy-retention gate.
Never use an INT8 engine merely because it is smaller or faster.

## Accuracy gate

Select the confidence threshold on the validation split, freeze it, and evaluate the
held-out test split once. Training, calibration and threshold selection must not consume
the held-out test set.

The project release minimums are:

| Metric | Minimum |
|---|---:|
| Person recall | 0.75 |
| Person precision | 0.60 |
| Person mAP50 | 0.70 |
| Small-person recall | 0.60 |
| FP16 recall loss versus its `.pt` source | at most 0.01 absolute |
| FP16 mAP50 loss versus its `.pt` source | at most 0.01 absolute |
| Optional INT8 recall loss versus FP16 | at most 0.02 absolute |

These are VeriSwarm project gates, not universal medical or rescue certification
thresholds. Report real results even when they fail.

`small-person` must be defined before evaluation from image-space box area or the selected
dataset's published size bins. Record the exact definition in the report. Also report
real-aerial and synthetic-disaster results separately; do not average them into one score.

## Sustained Nano performance gate

Measure at least 15 continuous minutes and at least 4,500 completed frames after 50 warmup
frames. Use the frozen deployment input, camera/decode path and confidence threshold.

Required result:

| Measurement | Gate |
|---|---:|
| Sustained completed throughput | at least 5.0 FPS |
| TensorRT inference p95 | at most 180 ms |
| Capture-to-rescue-event p95 | at most 250 ms |
| Dropped/invalid frame ratio | at most 1% |
| Inference or pipeline errors | 0 |
| OOM/process restart | 0 |
| Minimum available system memory | at least 1 GiB |
| Swap growth during measured interval | 0 |
| Thermal throttling | none observed |

Measure p50, p95, p99 and maximum latency. Record model load time, process RSS, Jetson RAM,
GPU/CPU temperature, power and clock/throttle state. A mean FPS number alone is not enough.

The gate covers discovery inference, not collision avoidance. Flight safety still uses the
independent depth/LiDAR supervisor if an inference frame is late or missing.

## Jetson procedure

Record, without installing a generic desktop CUDA/PyTorch build:

```bash
cat /etc/nv_tegra_release
dpkg-query -W nvidia-jetpack nvidia-l4t-core tensorrt 2>/dev/null
nvpmodel -q
uname -a
python3 --version
```

Select the available 15 W profile by its displayed name; do not hardcode a mode number
that may differ across images. Keep the fan/thermal setup fixed. Capture `tegrastats`
during both the engine-only and full-pipeline runs.

Benchmark phases:

1. Accuracy-equivalence check for `.pt` versus FP16 engine on identical held-out inputs.
2. Engine-only latency to isolate TensorRT behavior.
3. Full camera-to-event pipeline for the sustained gate.
4. Offline restart with the network disabled and every required artifact already local.
5. One camera timeout and one corrupt-frame test; both must fail safely without crashing
   the mission process.

## Security and release binding

The approved-model allowlist and every deployment receipt must bind the artifact that is
actually executed:

- final TensorRT engine SHA-256;
- source `.pt` and ONNX SHA-256 lineage;
- class map and confidence threshold;
- static input shape and precision;
- JetPack/TensorRT/runtime bundle identity;
- benchmark report and accuracy report IDs.

Hashing only the training `.pt` while executing an unbound `.engine` does not prove model
identity. Abhijan's final clean/model-swap demonstration must use the selected engine hash
or explicitly remain labelled as a legacy PyTorch model-hash demonstration.

## Portability statement

If this complete pipeline passes on Orin Nano 8GB at 15 W, it establishes a conservative
entry-level Jetson baseline. Larger Jetsons are expected to offer more resources, but each
target still requires a rebuilt engine and a smoke/performance test on its own JetPack,
TensorRT, camera and thermal configuration. Do not claim binary compatibility from compute
performance alone.

## Primary references

- NVIDIA Jetson module lineup:
  <https://developer.nvidia.com/embedded/jetson-modules>
- NVIDIA Jetson Orin Nano getting-started/power-mode guide:
  <https://developer.nvidia.com/embedded/learn/get-started-jetson-orin-nano-devkit>
- NVIDIA TensorRT documentation:
  <https://docs.nvidia.com/deeplearning/tensorrt/latest/index.html>
- Ultralytics TensorRT export documentation:
  <https://docs.ultralytics.com/integrations/tensorrt>

References accessed 2026-08-22.

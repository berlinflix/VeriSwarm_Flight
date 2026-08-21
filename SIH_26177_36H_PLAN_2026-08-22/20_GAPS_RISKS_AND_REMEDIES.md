# Current gaps, risks and remedies

## P0 architectural gaps

| Gap now | Why it matters | 36-hour remedy | Owner |
|---|---|---|---|
| `MissionRunner` sends perception-derived action as the whole command | no actual search/waypoint autonomy | add route intent + metric local planner; supervisor gates final combined command | Pratik |
| `yolo_action.py` treats every box as an obstacle | a person detection can cause nonsensical steering | add observation-only rescue adapter; depth/LiDAR owns obstacle safety | Samik + Pratik |
| FactoryCity flight is straight and deliberately non-avoiding | does not satisfy disaster search | sector partition + lawnmower path + HOLD/bounded A* | Pratik |
| no person geolocation or deduplication | dashboard boxes are not actionable rescue locations | pose/calibration/depth projection with uncertainty and track/map dedup | Pratik + Suyash |
| dashboard is model-hash oriented | does not show coverage, people, hazards or responder priorities | rescue event projection and map mode | Suyash |
| no accepted disaster model | COCO person is not an aerial-SAR qualification | narrow RGB person fine-tune with held-out evaluation | Samik |
| no thermal model/runtime | problem statement expects thermal support | HIT-UAV specialist; synthetic Cosys thermal clearly marked; real sensor remains future | Samik + Pratik |
| no hazard model | cannot claim fire/flood/debris classification | P1 FloodNet segmentation; route blockage from metric occupancy; truth-only visuals otherwise | Samik + Abhijan |
| no situational report | detections alone do not help responders prioritize | deterministic alert engine and JSON/HTML report | Suyash |
| simulator and protocol are separate | trust decision does not affect flight | pass model authorization into mission manager and require HOLD/reassignment | Suyash + Pratik |

## P1 credibility gaps

| Gap | Honest position now | Later remedy |
|---|---|---|
| GPS-denied navigation | absent; simulator truth is not VIO | calibrated stereo/mono VIO, IMU time sync, estimator health and loop-closure evaluation |
| autonomous mapping | coverage grid and hazard map can be built, but not SLAM | occupancy/semantic mapping with pose covariance and map consistency tests |
| RGB–thermal fusion | separate specialist observations only | calibrated paired sensor, time sync, cross-modal association and held-out fusion evaluation |
| chemical leak | vision alone is unreliable | gas sensor payload plus plume model and sensor fusion |
| exposed power line | no suitable accepted class/model | dedicated high-resolution dataset/sensor and small-object evaluation |
| structural instability | visual damage is not engineering stability | damage segmentation as triage only; human/structural-engineer confirmation |
| safe route for responders | drone route is not a ground-team path | terrain traversability model and responder constraints |
| Qualcomm deployment | no Snapdragon target presently evidenced | compile/profile ONNX through Qualcomm AI Hub/QNN on named hardware |
| real flight | simulation only | PX4/SITL, HIL, controlled flight envelope and aviation safety process |

## Dataset/model risks

- **Terms/licence ambiguity:** do not wait to download or privately evaluate. Record the
  Kaggle owner/version/URL and `TERMS_NOT_DISPLAYED`; keep data and derived weights private
  until release rights are reviewed. Hash only the final selected release artifact.
- **Kaggle provenance:** combined/web-scraped sets can contain duplicates, mislabels and
  unknown source overlap. Use them for rapid internal models, then cross-test on official
  aerial datasets and never use their supplied test alone as proof.
- **Classification/detection confusion:** disaster scene folders cannot localize a person,
  fire edge or flood polygon. Use boxes for alerts and masks for maps.
- **Mixed incomplete labels:** keep specialist datasets/models; never create false negatives
  by merging partially labelled datasets naively.
- **Domain shift:** report real-dataset and synthetic-simulator metrics separately.
- **Small targets:** use higher input resolution/tiling only if edge latency still passes.
- **Leakage:** split by scene/video, not random adjacent frames.
- **Test overfitting:** model/threshold selection uses validation only.
- **Cloud provenance:** local reproduction is mandatory for the selected model; hash that
  final checkpoint once when it enters the demo allowlist, not after every cloud run.
- **False confidence:** dashboard displays confidence and uncertainty, not a binary rescue
  verdict.

## Simulator risks

- **Large world rebuild:** reuse derived FactoryCity; do not migrate to another simulator.
- **Niagara load:** use a light sprite/flipbook if smoke reduces stable FPS.
- **Invisible collision volumes:** retain collision visualization/probes.
- **Coordinate mix-up:** one transform manifest and vehicle-local command conversion tests.
- **Thermal realism:** Cosys IR is segmentation/temperature-response based; label synthetic.
- **Five-drone instability:** freeze a two-drone functional fallback at H24.
- **Truth leakage:** evaluator runs after mission and outside autonomy import paths.

## Security/trust risks

- A valid signature proves origin/integrity, not semantic truth.
- A model allowlist proves approved bytes, not model accuracy.
- Only Alpha currently has a hardware-backed key.
- OP-TEE does not protect model loading, preprocessing, pose estimation or flight control.
- Three-node quorum has no Byzantine fault tolerance (`f=0`).
- The current model-hash attack is a deterministic policy case, not live hot-swapping.

Keep these statements in the pitch. VeriSwarm's differentiator is accountable, fail-closed
rescue intelligence—not a claim of perfect or unhackable autonomy.

## Schedule risks with seven Codex sessions

The limiting resources are GPU time, downloads, Unreal packaging, physical camera devices
and integration—not code generation. Remedies:

- exclusive file ownership and one schema freeze;
- 5-epoch training probes before long runs;
- downloads and training in parallel;
- integration samples from hour 4, not hour 24;
- two-drone fallback frozen at hour 24;
- hard feature freeze at hour 30; and
- one independent lane devoted to tests/evidence instead of more features.

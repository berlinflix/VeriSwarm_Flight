# MESSAGE — synthetic POV input and NVIDIA deployment pipeline

**From:** Suyash  
**To:** Samik, Ayush, Pratik, Abhijan  
**Date:** 2026-08-22  
**Priority:** P0 integration lane  

Read `docs/SYNTHETIC_DRONE_POV_INPUT.md` and continue without waiting for a routine
authorization reply.

We are adding a cinematic disaster drone-POV MP4 as a continuous RGB camera source. The
display remains at the clip's native rate (target 24 FPS); inference runs on the newest
frame at the sustainable device rate, with no stale-frame queue. Generated-video results
must be labelled synthetic and kept separate from real-aerial held-out metrics.

Samik owns the rescue-detector runner, newest-frame sampling, source timestamps, tracking,
event emission and RTX/Jetson benchmark. Train on the cloud NVIDIA GPU with CUDA/AMP,
export ONNX, then build TensorRT FP16 on the actual Orin Nano. Do not move an RTX TensorRT
engine to Jetson.

Ayush owns the smooth annotated 24 FPS presentation and visible inference/track age, as
Samik's backup. Pratik keeps CoSys authoritative for flight, collision, pose and depth.
Abhijan owns manual clip review/annotations and dashboard interpretation. Generated
alternate angles cannot be used for metric triangulation without real calibration.

Use `node.frame_source.VideoFileSource` for the MP4 adapter. Post progress or blockers as
new files in `docs/team_updates/` and check the folder at least every 30 minutes.

# Ayush — USB webcam + Android DroidCam execution plan

**Date:** 19 August 2026  
**Primary operator:** Ayush  
**Technical owner/overseer:** Samik  
**Attack-artifact operator:** Abhijan  
**Final GO/NO-GO:** Suyash

## Outcome

Deliver three repeatable clean→attack→recovery cycles using one USB webcam and
one Android phone on a tripod through DroidCam. Retain measured evidence and
prove both camera sources are released before the Jetson becomes Alpha.

## Ayush's tasks

1. Label and rigidly mount Camera A (USB) and Camera B (Android).
2. Configure the phone in landscape/rear-camera mode, powered, Do Not Disturb,
   rotation locked and DroidCam battery optimisation disabled.
3. Record the exact USB `/dev/video*` or `/dev/v4l/by-id/*` source and exact
   DroidCam `/dev/video*` or local HTTP URL.
4. Frame one textured shared scene and a detector-supported object. Do not move
   the cameras after command freeze.
5. Run the feature-only alignment command with Samik, obtain `COVISIBLE` and
   confirm the RANSAC-projected `view_IoU` is stable. Never compare raw boxes
   from the two different pixel coordinate systems.
6. Run the pinned-model semantic command. Operate `c`, `a`, `r`, `s`, `q` only
   when both live panels show the real YOLO boxes/class/confidence and the
   displayed decision matches the physical condition.
7. Coordinate Abhijan's application/removal of the artifact without touching
   the cameras.
8. Complete three cycles, confirm the projected same-class `box_IoU` is retained
   when detections exist, exit with `release=True`, and give Samik the untouched
   run directory. `box_IoU=n/a` must remain unavailable, never rewritten as zero.
9. Close DroidCam and hand the Jetson to Samik for fresh OP-TEE preflight.

If DroidCam cannot pass the setup gate within ten minutes, Ayush keeps the same
Android phone/tripod and switches Camera B to a prevalidated local MJPEG/RTSP
phone stream that OpenCV can open. He records the new URL/app/transport, uses a
new run ID and discards no evidence. Internet relays and source changes during a
run are prohibited.

## Samik's oversight tasks

1. Review the Jetson Python/OpenCV/Torch/Ultralytics state; never install generic
   desktop PyTorch on Jetson.
2. Review and freeze camera sources, model hash, thresholds, command and run ID.
3. Confirm feature-only output is never described as semantic agreement.
4. Confirm `view_IoU` and same-class `box_IoU` are RANSAC-projected planar demo
   measurements, not raw cross-view box IoU, calibrated stereo or 3-D IoU.
5. Watch camera health, skew, inliers, measured claims and reason codes.
6. Reject a cycle caused only by camera loss, blur, stale frames or excessive
   skew.
7. Verify the evidence summary and release probe before accepting the handover.
8. Act as recovery operator if Ayush's terminal or DroidCam stream fails.
9. Start no Alpha/OP-TEE workload until the webcam process and both sources close.

## Stop conditions

Stop without weakening thresholds when:

- either camera cannot be identified unambiguously;
- Camera A and B resolve to the same source;
- clean frames are not co-visible;
- clean semantic claims are unstable;
- the attack outcome is only a camera-health failure;
- the model hash differs;
- evidence paths already exist;
- the release probe fails; or
- any camera/OP-TEE/Alpha process overlap occurs.

## Acceptance

- Three complete clean→attack→recovery cycles from one frozen command.
- Clean state reports `AGREE`.
- Attack reports `DISPUTE`, or an explicitly valid feature-overlap abstention;
  camera-health abstention does not count.
- Recovery returns to `AGREE`.
- Raw frames, annotated frames, JSONL and summary are present.
- Both panels show live class/confidence boxes from the same inference retained
  in JSONL; a numbers-only terminal is not acceptable.
- `summary.json` reports the required cycles and `release_verified=true`.
- Samik can independently repeat the command using only this plan and
  `codebase/docs/COVIS_LIVE.md`.

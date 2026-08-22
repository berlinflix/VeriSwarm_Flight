# MESSAGE — rescue video evaluation handoff

**From:** Suyash  
**To:** Samik  
**Date:** 2026-08-22  
**Priority:** P0 after the local training pipeline and first usable checkpoint  

Continue the training-pipeline and cloud work without waiting for a routine authorization
reply. The videos below are external evaluation/demo inputs; none belongs in a training,
validation or threshold-selection split.

## USB handoff

Copy the complete external folder:

```text
G:\SIH\RESCUE_POV_HANDOFF_2026-08-22\
```

Expected contents:

| File | Source label | Properties | Expected person result |
|---|---|---|---|
| `SYN-COLLAPSE-01.mp4` | `synthetic_rgb` | 1280x720, 24 FPS, 240 frames, 10 s | one candidate after reveal |
| `SYN-HAZARD-01.mp4` | `synthetic_rgb` | 1280x720, 24 FPS, 240 frames, 10 s | one candidate near smoke/debris |
| `NEGATIVE-NO-PERSON-PHYSICAL-01.mp4` | `physical_rgb_whatsapp_compressed` | 1024x576, about 29.94 FPS, 504 frames, 16.83 s | zero person candidates |
| `README.txt` and negative sidecar | metadata | text | read before execution |

The negative recording is an empty stairwell/corridor with a fire extinguisher. It is a
physical transport-compressed false-positive smoke test, not an aerial or disaster-domain
negative. Pratik will later supply a no-person CoSys disaster recording.

## Required runtime behavior

Use `node.frame_source.VideoFileSource` from Suyash commit `60317d3`.

- Preserve native frame order and source timestamps.
- Display/decode at native rate while inference consumes only the newest available frame.
- Never create a growing stale-frame queue.
- Expose the age of the last inference/track used for presentation.
- Keep synthetic, physical and later real-aerial video results separate.
- Do not invent pose, range, depth or NED location from these uncalibrated clips.
- The current model emits only `person_candidate`; smoke/debris is visual context, not
  proof of hazard classification.

## Outputs

For each input retain outside Git:

1. annotated MP4;
2. timestamped detection/track JSONL;
3. first-detection time and visible-track persistence;
4. thresholded candidate count and false-alert count;
5. decoded/displayed, inferred and dropped-frame counts;
6. decode FPS, inference FPS and capture/source-to-event latency distribution; and
7. runtime/model artifact identity used for the run.

For the negative clip, every thresholded `person_candidate` is a false alert. Preserve
failed runs and report the measured result rather than tuning on the clip and reusing it as
unseen evaluation.

Publish code, schemas, commands and summary metrics on `samik/sih26177-perception`.
Keep raw videos, annotated videos, weights, environments and generated evidence outside
Git. Update `STATUS_SAMIK.md` when the local pipeline, cloud smoke job or first measured
video run changes state.

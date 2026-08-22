# STATUS — SAMIK

Updated: 2026-08-22 14:00 IST

Branch: `samik/sih26177-perception`

Latest work commit: `15d1af6fca21c9b1b306c234390097b6d3f7e8b7`

## Completed since the previous update

- Selected RunPod Secure Cloud with one NVIDIA GeForce RTX 5090 (32 GB VRAM) as the
  training target; no Pod or cloud environment is ready yet.
- Froze the external execution plan at
  `C:\projects\VeriSwarm\My work\SAMIK_RESCUE_PERSON_MODEL_2026-08-22\IMPLEMENTATION_PLAN.md`.
- Selected official VisDrone2019-DET train and validation for the real aerial-person
  baseline and C2A v2 for later synthetic disaster-human refinement.
- Re-ran the focused adapter tests from the current branch using an external pytest
  temporary directory: `10 passed in 0.04s`. This is Codex verification, not Samik
  acceptance or model-training evidence.

## In progress now

- C2A v2 is still an unverified browser download at
  `C:\Users\Samik\Downloads\Unconfirmed 732194.crdownload`; observed partial size was
  `4,614,983,459` bytes at 2026-08-22 13:58 IST. It has not been moved, uploaded,
  extracted or counted as accepted data.
- Preparing the RunPod RTX 5090 environment and persistent storage gate. The planned
  persistent root is `/workspace/samik-rescue-person-model-20260822` on a 100 GB
  network volume.
- Preparing official VisDrone train/validation intake, deterministic one-class
  conversion, duplicate checks and the rendered label audit.

## Outputs available

- Frozen output class: `person_candidate` only.
- VisDrone input mapping: `pedestrian` and `people` -> `person_candidate`.
- C2A use: later refinement only; real VisDrone and synthetic C2A metrics remain
  separate.
- Verified VisDrone images downloaded: `0`; verified annotations downloaded: `0`.
- Converted images: `0`; converted labels: `0`.
- Corrupt-file checks completed: `0`; cross-split duplicate checks completed: `0`.
- Planned first audit path:
  `/workspace/samik-rescue-person-model-20260822/label-audit/visdrone-person-100/`;
  not created yet.
- Five-epoch smoke job: **NOT STARTED**. Planned persistent output root:
  `/workspace/samik-rescue-person-model-20260822/runs/training/`.
- Latest detector metrics: **NONE**. No baseline, smoke, validation, test or P2 latency
  metric exists yet.

## Blockers

- No RunPod or Kaggle credential environment variables are present in the current
  shell, and `runpodctl` is not installed. This does not block local preparation or
  public VisDrone intake; cloud provisioning requires an authenticated RunPod access
  path.

## Shared-interface changes

- NONE. The detector still emits only `person_candidate` through Suyash's frozen
  `veriswarm.rescue.event.v1` schema. No schema, five-camera, security or network
  evidence was changed.
- Candidate `15d1af6` remains unreviewed and still lacks model ID/hash and class-map
  binding, stale-frame rejection and runtime failure handling.

## Next checkpoint

- Establish the RunPod RTX 5090 Pod and immutable environment identity, then download
  and verify VisDrone train/validation, publish counts and hashes, complete the
  100-image label audit, and start the five-epoch YOLOv8n smoke gate.

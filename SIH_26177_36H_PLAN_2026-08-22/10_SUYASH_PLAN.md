# Suyash — product, trust, integration and command-center plan

## Mission

Own the one coherent product. Freeze what the team may claim, integrate the rescue events
with the dashboard and VeriSwarm trust layer, accept evidence and stop parallel work from
turning into incompatible demos.

## H0–H2: freeze the contract

- [ ] Declare mission ID `OP-VARUNA-001` and one coordinate frame: NED metres.
- [ ] Freeze P0 capability claims and unsupported classes.
- [ ] Freeze minimum demo roster: five preferred, two-drone functional fallback.
- [ ] Assign seven Codex lanes and exclusive file ownership.
- [ ] Create a clean integration worktree; do not use the dirty Suyash checkout.
- [ ] Publish branch bases and require every owner to report exact commit and dirty status.
- [ ] Freeze event v1, model registry v1 and scenario manifest v1 from the architecture file.

Decision to publish immediately:

```text
We detect person candidates, not confirmed survivors. RGB/thermal detection, hazard
segmentation, obstacle avoidance and trust verification are separate measured subsystems.
Only capabilities that pass the acceptance matrix appear as live claims.
```

## H2–H10: build the rescue data plane

- [ ] Add/own the append-only rescue event collector.
- [ ] Validate schema, mission ID, node sequence, monotonic freshness and evidence hashes.
- [ ] Reject duplicate event IDs and incompatible schema/model versions.
- [ ] Add deterministic alert rules; keep raw observations visible beneath merged markers.
- [ ] Define report JSON and human-readable output.
- [ ] Extend the dashboard with:
  - mission map/search grid;
  - per-drone assigned/visited cells;
  - person-candidate markers with confidence and uncertainty;
  - hazard polygons/markers and responder-access status;
  - alert priority queue;
  - drone state, link state and current task;
  - model ID/hash and trust decision; and
  - evidence link/run ID.
- [ ] Preserve the existing model-hash dashboard and protocol demo as a separate mode.

The dashboard may initially consume a tailed JSONL file or local SSE service. It must not
invent movement, detections, hazards or verdicts when upstream data is absent.

## H8–H18: trust and integration

- [ ] Define the approved rescue-model hash allowlist and class-map hash.
- [ ] Ensure an unapproved model produces `model_rejected` and HOLD.
- [ ] Do not claim OP-TEE protects inference; it protects Alpha's signing key.
- [ ] Add frame/model/config/pose references to observation evidence.
- [ ] Verify the final command—not merely a detector action—is supervisor-gated.
- [ ] Receive Samik's model bundle and reproduce model load/class names/hash.
- [ ] Receive Abhijan/Pratik's scenario and verify world/settings/truth hashes.
- [ ] Run the first one-frame simulator-to-dashboard vertical slice by H14.

## H18–H26: mission orchestration

- [ ] Add the clean and unapproved-model cases to a create-once campaign runner.
- [ ] Confirm events buffer locally when the dashboard link is absent and sync idempotently.
- [ ] Confirm quarantine reassigns only unvisited cells.
- [ ] Generate a responder report containing coverage, unresolved cells, people, hazards,
      confidence/uncertainty, evidence and review priority.
- [ ] Decide at H24 whether five-drone runtime is credible; freeze two-drone fallback if not.

## H26–H36: acceptance and presentation

- [ ] Freeze all commits, models, configs, commands and hashes at H30.
- [ ] Run the failure matrix before the clean rehearsals.
- [ ] Conduct cold rehearsal 1 and inspect evidence before cold rehearsal 2.
- [ ] Record a fallback video from an accepted run; never stage a fake live result.
- [ ] Prepare a 90-second and a 4-minute narration.
- [ ] Keep a claim/evidence table visible to the team.
- [ ] Prepare reset, abort, port, process and artifact cards for every machine.

## Your acceptance checklist

- [ ] Every dataset/model has a source, owner/version, hash and displayed-terms state.
- [ ] Anything marked `TERMS_NOT_DISPLAYED` or `EXPLORATORY_ONLY` remains private, is not
      redistributed and is not presented as release-qualified evidence.
- [ ] No test split influenced threshold/model selection.
- [ ] Dashboard values trace to append-only events.
- [ ] All coordinates declare frame and uncertainty.
- [ ] No semantic box directly commands flight.
- [ ] Clean run reaches mission completion without collision/geofence violation.
- [ ] Wrong model is held for the correct reason.
- [ ] Two unchanged cold runs pass.
- [ ] Presenter language matches measured evidence.

## Do not spend time on

- changing the old Q-B evidence;
- retraining models yourself;
- a new UI framework rewrite;
- claiming every problem-statement bullet;
- adding GPS-denied claims without a real estimator; or
- moving the dashboard to cloud hosting.

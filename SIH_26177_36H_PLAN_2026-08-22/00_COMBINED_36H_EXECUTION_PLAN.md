# Combined 36-hour execution plan

## 1. Frozen outcome

At hour 36 the team must be able to run one command sequence that demonstrates:

1. five simulated drones start from a deterministic reset state;
2. the search polygon is divided into five bounded sectors;
3. each healthy drone follows a deterministic coverage route;
4. a person candidate is detected by an accepted edge model, tracked, geolocated and
   shown once on the command map;
5. at least one **measured** hazard type is mapped; the minimum accepted hazard is a
   blocked route or flood/water mask;
6. depth/LiDAR prevents an unsafe command near rubble and the route either replans or
   enters a bounded HOLD—never blindly continues;
7. one model-provenance attack is rejected by VeriSwarm and releases HOLD, not motion;
8. unfinished cells belonging to an unavailable/quarantined drone are reassigned;
9. the dashboard shows coverage, drone state, alerts, evidence provenance and mission
   completion; and
10. the entire mission works without cloud inference.

The previous five-camera co-visibility application and cyber-attack campaigns are retained
as independent VeriSwarm capability stages under `04_PRESERVED_VERISWARM_CAPABILITIES.md`.
They must not be deleted, relabelled as rescue-flight evidence or allowed to block the P0
rescue thin slice.

If five-drone integration is not stable by hour 24, the accepted fallback is a fully
functional **two-drone mission** with the same data, navigation, safety, mapping and
alerting contracts. Five moving pawns without an end-to-end data path are not preferable
to two functioning autonomous agents.

## 2. Scope hierarchy

### P0 — must work

- RGB person-candidate detector with a frozen held-out evaluation.
- Deterministic search-sector and coverage-path generation.
- Simulator RGB, depth and pose ingestion.
- Obstacle stop/HOLD and one bounded bypass/replan behavior.
- Observation geolocation and spatial deduplication.
- Append-only mission events and command-center map.
- Model hash, class map, thresholds and latency in every inference event.
- One clean mission and one unapproved-model rejection mission.

### P1 — add after P0 passes

- Thermal person detector using a real thermal UAV dataset.
- Flood/water/road-access semantic segmentation.
- Five-drone work reassignment and offline event buffering.
- Automatic JSON situational report with priority ordering.

### P2 — presentation stretch only

- Fire/smoke detector from Kaggle for the internal prototype, cross-tested separately on
  aerial/simulator frames; aerial qualification remains a separate gate.
- Synthetic Cosys thermal view, clearly labelled synthetic.
- Qualcomm QNN compilation/profile on actual supported hardware.
- GPS-denied VIO/SLAM. Do not simulate GPS denial by secretly reading simulator truth.

## 3. Seven parallel Codex lanes

| Lane | Human owner | Exclusive code surface | Deliverable |
|---|---|---|---|
| C1 | Samik | dataset manifests, RGB training/evaluation | frozen RGB person model |
| C2 | Samik, with Ayush on trigger | thermal/flood conversion and training | thermal or flood specialist |
| C3 | Abhijan | scenario specification, truth and evaluation assets | disaster scenario contract |
| C4 | Pratik | Unreal/CoSys world and sensor capture | deterministic runnable world |
| C5 | Pratik | coverage planner, fleet mission and safety adapter | autonomous mission runner |
| C6 | Suyash | event schema, dashboard, alert/report pipeline | command-center experience |
| C7 | Suyash-appointed reviewer | integration tests, campaign runner, runbook | independent acceptance evidence |

Preserved-capability ownership remains: Ayush designs the bounded five-camera UI changes,
Samik oversees and executes that runtime, Abhijan owns attack execution, and Suyash accepts
security policy and presentation language.

No lane may edit another lane's owned files without an explicit handoff. Shared schemas
are frozen in `01_ARCHITECTURE_AND_INTERFACES.md`; changes require Suyash and both affected
owners to acknowledge the new schema version.

## 4. Git and artifact discipline

Create clean worktrees from the accepted integration base. Suggested branches:

```text
suyash/sih26177-rescue-integration
samik/sih26177-perception
abhijan/sih26177-scenario-contract
pratik/sih26177-disaster-cosys
pratik/sih26177-rescue-autonomy
ayush/sih26177-perception-support
codex/sih26177-independent-verification
```

Each branch must have a narrow file-ownership list in its first commit. Never copy a
weight, dataset, absolute machine path, credential, private manifest or generated evidence
into Git. Every external artifact receives a manifest containing source, displayed terms,
byte-size and SHA-256.

Merge order:

1. interfaces and schemas;
2. scenario contract and dataset converters;
3. model inference adapter and simulator adapter;
4. planner, mapper and mission manager;
5. dashboard/report pipeline;
6. campaign/runbook only after all focused suites pass.

## 5. Thirty-hour build schedule plus six-hour margin

| Time | Required output | Parallel owners |
|---|---|---|
| H0–H1 | freeze scenario, claim language, P0 taxonomy, branch/file ownership | Suyash + all |
| H0–H3 | Kaggle/official source-version-terms inventory and first simulator truth manifest | Samik, Abhijan |
| H1–H5 | dataset download/conversion; derived disaster level skeleton; interface stubs | Samik/Ayush, Pratik/Abhijan, Suyash |
| H4 gate | RGB data loader renders 100 correct labels; world boots/reset twice | Samik, Pratik |
| H5–H10 | RGB baseline and first fine-tune; RGB/depth/pose capture; sector planner | Samik, Pratik |
| H8 gate | one drone executes a search path with no detector dependency | Pratik |
| H10–H16 | model evaluation/export; person target integration; geolocation/dedup; dashboard event ingestion | Samik, Pratik, Suyash |
| H14 gate | one simulator frame produces a visible, traceable person-candidate event | all integration owners |
| H16–H22 | obstacle behavior; hazard mapping; five-drone partition/reassignment; edge benchmark | Pratik, Samik, Suyash |
| H20 gate | two-drone end-to-end thin slice passes from cold reset | all |
| H22–H26 | five-drone integration, model-hash rejection beat, offline buffer/report | all |
| H24 decision | freeze two-drone fallback if five-drone path is unstable | Suyash |
| H26–H30 | failure campaigns, bug fixes, full tests, artifact hashes, final feature freeze | all |
| H30 | **hard feature freeze** | Suyash |
| H30–H33 | cold rehearsal 1, evidence review, fix only release-blocking defects | all |
| H33–H35 | unchanged cold rehearsal 2, fallback video and offline bundle | all |
| H35–H36 | pitch, architecture diagram, metric table, machine reset cards | all |

## 6. Cross-team handoffs

### Abhijan → Pratik by H3

- scenario ID and world-copy ID;
- search polygon and safe launch/landing areas in one declared frame;
- person target actor names and ground-truth positions;
- hazard actor names, types, severities and polygons;
- blocked and safe route geometry;
- exact expected mission events;
- reset procedure and forbidden edits to original FactoryCity.

### Samik → Pratik and Suyash by H12

- model registry entry and exact class map;
- preprocessing contract, image size and color order;
- inference API example with deterministic output schema;
- frozen threshold chosen on validation data;
- model and ONNX SHA-256;
- held-out metrics and P2 latency;
- classes that the model does **not** support.

### Pratik → Suyash by H18

- simulator adapter command;
- deterministic world/settings/config hashes;
- emitted RGB/depth/pose and mission-event samples;
- search-path and safety evidence;
- reset/abort procedure;
- known simulator-only assumptions.

### Suyash → everyone by H20

- accepted event schema and dashboard endpoint;
- mission campaign IDs;
- exact clean and attack expected outcomes;
- evidence directory convention;
- GO/NO-GO checklist and demo cues.

## 7. Stop conditions

Stop a run, preserve it and use a new run ID if:

- the model/dataset/config hash differs from the frozen manifest;
- a detector class is claimed without held-out labels and metrics;
- simulator pose or segmentation truth leaks into the autonomy input where an estimated
  value is claimed;
- a person box directly produces a flight command;
- depth/LiDAR is stale or absent while motion is requested near obstacles;
- the coordinate frame is ambiguous;
- a drone collision, geofence breach, stale command or duplicate identity occurs;
- an evidence path already exists;
- dashboard markers cannot be traced back to a source frame, model and pose; or
- the camera, simulator, peer or OP-TEE process remains owned after its stage exits.

## 8. Definition of done

The build is done only when two unchanged cold rehearsals produce create-once evidence,
zero unapproved motion, traceable alerts, the advertised model metrics and a clean reset.
The final presentation must distinguish real measurements, simulated sensors, simulator
truth used only by the evaluator, and roadmap capabilities.

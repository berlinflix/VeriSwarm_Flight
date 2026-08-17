# Pratik — standalone execution plan

**Role:** M3, Cosys environment and sensor-truth owner  
**Primary outcome:** deliver one deterministic, operationally realistic Cosys scenario and
sensor interface that another teammate can reproduce without editor state, hidden paths or
verbal assumptions.

This document contains Pratik's work only. Other names identify required handoffs/reviews,
not tasks Pratik should absorb.

## 1. Non-negotiable boundaries

- Build exactly one scenario: **Contested Border Sentinel**.
- Start from a pinned prebuilt Neighborhood/City-style Cosys environment. Do not spend the
  schedule modelling terrain from scratch.
- Pratik owns what drones see, not how they decide or move. Do not implement flight control,
  VIO, A*, task allocation, consensus or command release.
- Never teleport vehicles as accepted flight and never manually pilot an accepted route.
- Never expose simulator true pose, segmentation/object IDs or evaluation annotations to
  the autonomy process.
- Treat rocks, trees, buildings and walls as geometric obstacles through depth/LiDAR even
  when YOLO has no label for them.
- Use a validated detector class for the semantic adversarial target. Generic COCO weights
  do not justify weapon/special-equipment claims.
- Every environmental change must be represented in a versioned scenario manifest and
  deterministic seed—not an editor-only action.

## 2. Files and artifacts Pratik owns

| Path/artifact | Required purpose |
|---|---|
| `codebase/sim/cosys/contested_border/` | Exported scenario layer and reproduction assets |
| `settings.json` in the scenario bundle | Explicit five-vehicle and sensor configuration |
| `world_manifest.json` | Base environment, asset, weather, fault-zone and scenario hashes |
| `calibration_manifest.json` | Intrinsics/extrinsics, frames, rates, noise and skew bounds |
| `truth_schema.json` and truth exporter | Isolated scoring data, inaccessible to autonomy |
| `scenario_modes.json` | Smoke, clean baseline and named campaign modes |
| `data/model_selection/` | Ignored raw tune/held-out frames and labels |
| `docs/PRATIK_COSYS_REPRODUCTION.md` | Clean installation, launch, reset and export procedure |

Use repository-relative paths inside manifests. Machine-specific paths belong only in a
local override that is never required by another PC.

## 3. Ordered implementation plan

### P0 — pin the environment and installation

1. Record the exact Cosys-AirSim repository URL, commit/release, archive checksum, Unreal
   version, Python client version, GPU driver, OS build and vehicle controller/firmware.
2. Choose one shipped Neighborhood/City-style environment and record its exact identifier.
3. Always launch with an explicit settings path. Record the actual resolved file in logs.
4. Create a clean export directory containing the scenario layer, settings, required assets
   and hashes. Do not depend on an unsaved Unreal editor state.
5. Cold-start the exported build twice and record startup time, warnings and failures.

**Gate P0:** the scenario opens from the exported bundle after reboot with no missing asset,
manual editor repair or internet download.

### P1 — make the immediate flight handoff

Before adding attacks or complex terrain, supply Samik with:

1. API host/port and firewall/LAN requirements.
2. Raw `client.listVehicles()` output.
3. Exact case-sensitive names `alpha`, `bravo`, `charlie`, `delta`, `echo`.
4. `VehicleType`, initial pose, sensor list and collision state for every vehicle.
5. Clear NED A and B coordinates, permitted altitude band, geofence and distinct five-drone
   rally offsets `B_i`. Document that altitude above the NED origin uses negative `z`.
6. One RGB/depth capture and `getMultirotorState()` result per vehicle.
7. A deterministic `smoke` mode with attacks, GNSS faults and moving obstacles disabled.
8. Reset instructions and the expected post-reset roster/pose/collision snapshot.

Do not declare the route clear by visual inspection only. Use collision geometry and depth
captures to establish the smoke corridor, then retain those results.

**Gate P1:** Samik's read-only client enumerates the expected roster remotely and the same
A/B handoff works after three cold resets without Pratik touching the editor.

### P2 — configure all sensors and calibration

For every vehicle, configure and document:

- front and/or nadir RGB needed by the mission;
- metric depth perspective or LiDAR used for navigation;
- IMU, barometer, magnetometer and GNSS;
- image size, FOV/intrinsics, distortion assumption and colour format;
- camera/sensor position and yaw/pitch/roll in the vehicle body frame;
- sample rate, noise, latency, units and coordinate convention;
- measured RGB/depth/pose timestamp skew and the maximum accepted skew;
- stable calibration ID/hash.

Produce a `SensorSnapshot` sample containing sensor timestamps, source metadata, health,
calibration ID and pairing skew. If acquisition is not atomic, measure the gap rather than
calling it atomic.

Create coordinate fixtures for Unreal/world, Cosys NED, vehicle body and camera optical
frames. Round-trip known points and axes; include a visible orientation fixture so sign or
yaw mistakes are detectable.

**Gate P2:** all five sensors enumerate at declared rates; calibration round trips pass;
skew stays inside the declared limit or the sample is marked invalid.

### P3 — build the single integrated operational scenario

Use one world and one continuous mission spine:

| Checkpoint | Scene responsibility |
|---|---|
| A | Five separated launch points, safe vertical corridors and preflight visibility |
| C1 | Border surveillance sector, restricted region, noisy ground cue, people/vehicles |
| C2 | Static and dynamic geometric obstacles, alternate route and fully blocked variant |
| C3 | GPS-healthy→denied→reacquisition region and separate spoof-transition region |
| C4 | Named physical patch/model/protocol/C2 attack placement or trigger boundary |
| B | Distinct safe rally/landing positions `B_i`, not one shared coordinate |

Add day/night and declared weather variants inside the same scenario rather than creating
another world. Include roads, vegetation, buildings, occlusion, glare/shadow, civilian
traffic, people that split/merge, stopped/moving vehicles, a casualty/SAR cue, rough
geometry, narrow but traversable corridors and a no-path configuration.

Keep the system unarmed: targets produce surveillance/confirmation alerts, not engagement
commands.

**Gate P3:** every named object/fault zone has a stable scenario ID, seed and manifest
entry; mission checkpoints can be reset and replayed without editor intervention.

### P4 — ground sensors and behavior truth

1. Add ground-sensor cues with sensor ID, timestamp, health, measured location uncertainty
   and configurable false-positive/missed-cue behavior.
2. A cue creates an inspection task only; it must not become a confirmed target.
3. Define evaluation-only behavior events such as restricted-zone entry, border crossing,
   loitering and group/convoy movement using explicit temporal/spatial rules.
4. Record true event start/stop, actor identity and path only in isolated truth.
5. Include negative cases—civilian passage, stationary objects, shadows and false cues.

**Gate P4:** truth can score detection/track/alert correctness while the autonomy API sees
only sensor measurements and cue messages.

### P5 — GPS, C2 and environmental fault zones

Create separate, named regions/profiles for:

- healthy GNSS;
- complete GNSS denial;
- gradual spoof drift;
- step spoof jump;
- reacquisition;
- C2 latency/loss/partition;
- wind/weather/camera degradation;
- dynamic route blockage.

The zone definition and true injected signal go only to truth logs. The estimator receives
the declared simulated sensor output. Do not tailor faults to force a desired verdict.

**Gate P5:** entering/exiting each zone produces reproducible raw sensor effects with
truth timestamps suitable for independent scoring.

### P6 — physical adversarial scene support

1. Place patches/occluders as actual 3-D geometry/materials visible through the simulated
   cameras. Never composite them into returned pixels.
2. Record parent object, size, pose, material/texture hash, lighting, range and viewpoints.
3. Confirm the semantic target is a supported/validated detector class from every required
   clean viewpoint before using it for adversarial evidence.
4. Preserve rocks/walls/trees/buildings as negative semantic but positive geometric cases.
5. Supply clean, attacked and occluded variants under deterministic scenario modes.

**Gate P6:** the clean target is detectable at the declared range/viewpoints; the physical
attack delivery is visible in raw frames; geometric navigation never depends on the label.

### P7 — model-selection and evaluation datasets

Capture a frozen tune set and a separately frozen held-out set containing:

- person and common vehicle positives at near/medium/distant scales;
- day/night, glare, blur, rain/fog and partial obstruction;
- varied viewpoints, occlusion, groups, civilian traffic and negative frames;
- rocks, trees, walls/buildings and confusing textures;
- matching calibration/scenario/seed/frame IDs.

Keep raw data outside Git under the ignored data directory. Commit a manifest containing
file hashes, labels, split IDs, class taxonomy, license/source status and collection
configuration. Freeze held-out IDs before benchmark results are seen; never move hard
examples into the tune set afterward.

**Gate P7:** every evaluation row maps to immutable frame/label/configuration hashes and no
held-out frame appears in training/tuning.

### P8 — truth isolation and scoring export

Produce evaluation truth for pose, collision, object tracks, behavior events, coverage,
fault injection and route occupancy. Enforce separation through one or more of:

- different process/API;
- separate output directory and credentials;
- offline post-run join using timestamp/run ID;
- tests proving autonomy imports cannot reach truth modules.

Truth logs must use synchronized timestamps and include missed/dropped truth records. A
truth failure invalidates scoring; it does not authorize using truth online.

**Gate P8:** an accepted autonomy process cannot request true pose, object IDs,
segmentation or expected outcomes, but the post-run scorer can deterministically join them.

### P9 — package and cross-PC reproduction

Deliver a content-addressed bundle containing:

- base environment identifier and license/source notes;
- scenario layer/assets and SHA-256 index;
- explicit settings and vehicle/sensor configuration;
- calibration/world/fault-zone/scenario-mode manifests;
- deterministic seeds and reset procedure;
- smoke A/B handoff and expected roster—not expected mission verdict;
- dataset/truth schemas and capture procedure;
- known warnings, performance requirements and fallback replay capture.

Give the bundle to Samik without editor files that are not in the archive. Observe his
reproduction but do not repair it manually; every missing step becomes a document/bundle
fix, then the reproduction starts again from clean state.

**Gate P9:** Samik reproduces the same environment/configuration hashes, roster, sensors,
reset state and smoke inputs on his PC using only written instructions.

## 4. Pratik's required test matrix

Pratik must test and retain evidence for:

- clean boot, reboot and deterministic reset;
- remote RPC connection and exact roster enumeration;
- every vehicle's initial collision/free state;
- RGB/depth/LiDAR validity and rates;
- camera/body/NED/world round trips;
- sensor timestamp skew and frame drops;
- day/night/weather modes;
- moving and newly blocked obstacles;
- fully blocked/no-path layout;
- ground-sensor true/false/missed cues;
- GNSS denial/spoof/reacquisition raw effects;
- C2 delay/loss/partition configuration;
- target clean visibility and adversarial object placement;
- truth isolation and offline join;
- export/import on Samik's PC.

## 5. Evidence Pratik must retain

For every scenario release retain UTC time, machine/driver versions, Cosys/Unreal hashes,
resolved settings path/hash, asset/world/calibration hashes, roster/sensor enumeration,
seed/mode, screenshots/video, raw sensor samples, fault-zone entry/exit, truth file hashes,
reset output, warnings and a complete artifact checksum index.

Never overwrite a failing scenario export. Assign a new version and retain the failure.

## 6. Pratik's immediate work queue

1. Freeze the selected Cosys/Unreal versions and prebuilt environment.
2. Deliver the P1 A/B flight handoff immediately.
3. Export and verify five named vehicles with safe separated poses.
4. Freeze sensor definitions and calibration.
5. Add C1–C4 and B scenario elements without breaking smoke mode.
6. Add truth isolation, ground cues and GPS/C2 profiles.
7. Capture/freeze the model-selection data.
8. Package the scenario and make Samik reproduce it from clean state.

## 7. Definition of done for Pratik

Pratik is done only when the single frozen scenario boots deterministically; all five
vehicles and calibrated sensors enumerate; A/B smoke inputs are unambiguous; surveillance,
terrain, GPS-denied, attack and SAR checkpoints exist as repeatable modes; truth is useful
for scoring but inaccessible to autonomy; datasets are frozen and hashed; every asset and
setting is exported; and Samik reproduces the same bundle on another PC without Pratik's
editor state or verbal intervention.

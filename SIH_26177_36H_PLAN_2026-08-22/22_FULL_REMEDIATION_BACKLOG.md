# Full remediation backlog — from 36-hour prototype to fieldable rescue system

This file expands every known gap into a concrete implementation and evidence requirement.
It is intentionally not time-boxed. A row is closed only when its verification evidence
exists; code completion alone is not closure.

## 1. Closure levels

| Level | Meaning |
|---|---|
| L0 | documented idea only |
| L1 | deterministic unit-tested software component |
| L2 | integrated simulation evidence across varied scenarios |
| L3 | hardware-in-the-loop and real-sensor bench evidence |
| L4 | controlled outdoor flight evidence with safety supervision |
| L5 | deployment-specific regulatory, safety and operational acceptance |

The 36-hour plan targets L1–L2 for a narrow capability set. “Fieldable” requires L4–L5
for the chosen aircraft, sensors, operating region and rescue agency workflow.

## 2. Perception and AI

### 2.1 RGB aerial person detection

**Current gap:** COCO is not an aerial-SAR qualification, and no accepted rescue model is
integrated.

**Add:**

- scene-grouped aerial-SAR train/validation/test datasets;
- small-object tiling or high-resolution inference where latency permits;
- person-only model registry entry, threshold and preprocessing contract;
- temporal tracking and repeated-observation confirmation;
- hard-negative mining for mannequins, debris, signs, reflections and vehicles;
- day/night, altitude, viewpoint, occlusion, weather and terrain error slices; and
- confidence calibration and explicit unknown/out-of-distribution state.

**Proof:** untouched test metrics, false alerts per flight-minute, miss rate by target size,
10-minute edge soak, field videos withheld by location/day, and operator review study.

**Owner:** Samik; Ayush independent audit. **Target:** L4.

### 2.2 Thermal person detection

**Current gap:** no real thermal camera, calibrated thermal pipeline or accepted model.

**Add:**

- radiometric or well-characterized thermal sensor;
- lens/FOV calibration and thermal dead-pixel/non-uniformity handling;
- HIT-UAV/WiSARD-class training with separate real-flight validation;
- ambient-temperature, time-of-day, altitude and occlusion tests;
- thermal-person model and fixed preprocessing; and
- clear distinction between radiometric temperature and display colorization.

**Proof:** real-sensor tests across hot/cold backgrounds, range-dependent recall, false
alarms from roofs/vehicles/fires and retained calibration records.

**Owner:** Samik + hardware owner. **Target:** L4.

### 2.3 RGB–thermal fusion

**Current gap:** separate observations are not calibrated multi-sensor fusion.

**Add:**

- rigid extrinsic calibration, intrinsic calibration and exposure timestamp synchronization;
- cross-modal registration with reprojection-error monitoring;
- association that permits RGB-only, thermal-only and fused tracks;
- fusion policy that degrades to a single healthy sensor instead of fabricating agreement;
- modality-specific uncertainty and health; and
- paired-data evaluation against each single-modality baseline.

**Proof:** calibration residuals, timestamp-skew distribution, fused versus RGB/thermal
precision-recall, failure tests with one sensor blocked/stale and no duplicated alerts.

**Owner:** Samik + Pratik. **Target:** L4.

### 2.4 Floodwater and traversability

**Current gap:** recognizing water pixels does not establish depth, flow or safe traversal.

**Add:**

- flood/water/road segmentation model;
- temporal mask stabilization and uncertainty;
- elevation/depth/terrain inputs for a separate traversability layer;
- safe stand-off policy near water boundaries; and
- responder-route model that distinguishes drone flight access from ground access.

**Proof:** held-out flood segmentation, boundary error, false water alarms, route decisions
against surveyed/simulated truth and conservative unknown handling.

**Owner:** Samik for segmentation; Pratik for map/planner; Suyash for responder semantics.
**Target:** L4.

### 2.5 Fire and smoke

**Current gap:** visual effects and simulator tags are not AI detection evidence.

**Add:**

- aerial fire/smoke data and negative scenes containing cloud, fog, dust, glare and
  sunsets; source/version/terms must be recorded, while unresolved release rights keep
  artifacts private rather than blocking internal experiments;
- separate smoke/fire localization or segmentation model;
- temporal confirmation and optical-flow consistency;
- thermal corroboration where available;
- distance/stand-off and downwind avoidance policies; and
- an unknown plume state rather than forced chemical classification.

**Proof:** aerial held-out evaluation, false alerts on confounders, time-to-detection,
sensor-ablation tests and safe route response.

**Owner:** Samik + Pratik. **Target:** L4.

### 2.6 Debris, blocked roads and damaged structures

**Current gap:** a semantic damage label does not prove structural instability.

**Add:**

- damage/debris segmentation with legal dataset review;
- metric occupancy and clearance measurement;
- road-blocked rule combining masks with traversable width;
- building-damage output labelled triage only; and
- human engineer/responder confirmation workflow.

**Proof:** held-out mask/road-blockage metrics, minimum-clearance accuracy, conservative
unknown behavior and zero “safe structure” automatic claims.

**Owner:** Samik + Pratik + Suyash. **Target:** L3/L4.

### 2.7 Landslides

**Current gap:** no landslide dataset, terrain-change model or slope-risk estimator.

**Add:**

- orthomosaic/elevation change detection;
- slope, terrain and recent-change layers;
- landslide segmentation trained on aerial data;
- uncertainty-aware exclusion polygon; and
- repeated survey comparison rather than single-frame certainty.

**Proof:** geographically held-out sites, boundary/area error, false alarm study and route
exclusion tests.

**Owner:** Samik + mapping owner. **Target:** L4.

### 2.8 Exposed electrical lines

**Current gap:** thin wires are extremely small and often invisible to RGB/depth at range.

**Add:**

- dedicated high-resolution wire/pole dataset;
- high-resolution camera or LiDAR suited to the minimum wire diameter/range;
- line-segment detector and temporal confirmation;
- map/no-fly corridor around known utilities; and
- “unknown clearance” HOLD when wire visibility is inadequate.

**Proof:** recall versus distance/background, miss analysis, sensor-resolution calculation
and controlled obstacle-course testing.

**Owner:** Samik + Pratik + hardware owner. **Target:** L4.

### 2.9 Chemical leaks

**Current gap:** RGB appearance cannot reliably identify most chemical leaks.

**Add:**

- appropriate gas/VOC/electrochemical sensor selected for target substances;
- calibration, cross-sensitivity and environmental compensation;
- plume sampling/search behavior with wind input;
- sensor-fusion alert with substance confidence/unknown state; and
- contamination stand-off/decontamination procedure.

**Proof:** controlled gas-simulator/approved test-source trials, calibration curves,
cross-sensitivity tests, plume localization error and fail-safe behavior.

**Owner:** future hardware/safety lead + Pratik. **Target:** L4/L5.

### 2.10 Model robustness and lifecycle

**Current gap:** one successful checkpoint is not a managed AI lifecycle.

**Add:**

- immutable model registry, signed release bundle and rollback protection;
- data/model cards, SBOM and training provenance;
- OOD detector and abstention;
- drift monitoring and field-error review;
- shadow evaluation before promotion;
- dual-bank rollback and canary deployment; and
- formal retirement/revocation of compromised models.

**Proof:** reproducible build, signature/hash validation, rollback exercise, corrupted model
tests, dataset shift suite and release approval record.

**Owner:** Samik + Suyash. **Target:** L5.

## 3. Localization, navigation and autonomy

### 3.1 Mission/waypoint controller

**Current gap:** the original mission loop has no waypoint intent and uses perception action
as the full request.

**Add:** deterministic mission state machine, sector/waypoint input, progress, loiter,
resume, return and landing states. Combine route intent with local obstacle constraints,
then gate the exact final command through the safety supervisor.

**Proof:** transition coverage, restart/resume tests, stale command tests and simulator
completion over varied routes.

**Owner:** Pratik. **Target:** L4.

### 3.2 GPS-enabled localization

**Current gap:** no documented estimator contract or integrity monitoring.

**Add:** GNSS + IMU + barometer fusion, covariance, innovation checks, jump/spoof alarms,
home-origin handling and map-frame transforms.

**Proof:** recorded trajectories against reference truth, outage/jump tests, error bounds
and automatic HOLD/transition when integrity fails.

**Owner:** Pratik. **Target:** L4.

### 3.3 GPS-denied VIO/SLAM

**Current gap:** absent; simulator truth cannot be described as VIO.

**Add:** calibrated camera–IMU estimator, hardware timestamps, initialization/relocalization,
loop closure, scale/gravity checks, estimator covariance and divergence monitor. Add terrain
matching or known landmarks only as an independently measured aiding source.

**Proof:** no-GNSS trajectories against simulator/motion-capture/reference truth, absolute
and relative trajectory error, loop-closure tests, blur/low-texture/darkness/failure cases,
and bounded safe behavior on estimator loss.

**Owner:** Pratik/Samik autonomy lane. **Target:** L4.

### 3.4 Mapping

**Current gap:** a marker list is not a consistent map.

**Add:** local metric occupancy map, pose-uncertainty propagation, semantic layers,
timestamped updates, map-frame versioning and persistent/temporary obstacle distinction.

**Proof:** occupancy/semantic IoU versus withheld truth, map consistency after loop closure,
stale-map eviction and no truth leakage.

**Owner:** Pratik. **Target:** L4.

### 3.5 Global and local planning

**Current gap:** no validated replanning; reactive box steering is insufficient.

**Add:** global A*/D* Lite or equivalent over a cost map; local dynamic-window/trajectory
optimizer or bounded grid planner; kinodynamic limits, inflation radius, uncertainty costs,
recovery behaviors and explicit no-path state.

**Proof:** path optimality/safety regression maps, dynamic obstacle tests, time budget,
clearance, geofence and no-path HOLD.

**Owner:** Pratik. **Target:** L4.

### 3.6 Dynamic obstacles and other aircraft

**Current gap:** static depth checks do not predict moving hazards.

**Add:** metric tracking, velocity prediction, time-to-collision, right-of-way/yield policy,
inter-drone deconfliction and emergency vertical/landing corridors.

**Proof:** crossing/head-on/overtaking simulations, sensor delay/dropout, minimum separation
and no oscillatory commands.

**Owner:** Pratik. **Target:** L4.

### 3.7 Energy and return safety

**Current gap:** no energy-aware task/return policy.

**Add:** battery state/health model, reserve calculation including wind/reroute, energy-aware
task allocation, return-to-home/alternate landing and lost-battery-telemetry HOLD/land.

**Proof:** discharge/wind payload tests, reserve never violated, emergency landing and
sensor-failure campaigns.

**Owner:** Pratik + hardware owner. **Target:** L4.

### 3.8 Landing-zone assessment

**Current gap:** landing uses frozen points, not disaster-area safe-zone detection.

**Add:** slope/roughness/obstacle/size assessment using depth/LiDAR, moving-person exclusion,
alternate zones and final descent recheck.

**Proof:** varied terrain/obstacle tests, false-safe rate, touchdown envelope and go-around.

**Owner:** Pratik. **Target:** L4.

## 4. Swarm coordination

### 4.1 Coverage and task allocation

**Current gap:** no accepted search allocator/reassignment in current flight product.

**Add:** deterministic sector partition, capability/battery-aware bidding, leases, visited
cell ownership, reassignment and conflict resolution.

**Proof:** coverage/time metrics, no double ownership, failures at different mission times,
late join and partition/reconnect tests.

**Owner:** Pratik. **Target:** L4.

### 4.2 Distributed map/event synchronization

**Current gap:** central dashboard projection is not a partition-tolerant swarm map.

**Add:** idempotent append-only events, per-node sequence/vector clocks where needed,
conflict rules, bounded caches, compression/prioritization and reconnect reconciliation.

**Proof:** loss/reorder/duplicate/partition campaigns and convergence to the same map.

**Owner:** Suyash + Pratik. **Target:** L4.

### 4.3 Inter-drone collision avoidance

**Current gap:** formation separation checks do not provide predictive deconfliction.

**Add:** reserved altitude/space-time corridors, shared intent, local sensing fallback,
priority/yield rules and separation assurance independent of network availability.

**Proof:** dense route crossings, link loss, stale peer intent and minimum-distance evidence.

**Owner:** Pratik. **Target:** L4.

### 4.4 Scalable trust/quorum

**Current gap:** three-node quorum has `f=0`; only Alpha has hardware identity.

**Add:** unique non-exportable key per aircraft, certificate/mission identity lifecycle,
authenticated encryption, replay protection, membership changes and a quorum sized for the
declared fault model. Byzantine tolerance requires an explicit protocol and sufficient
independent members; do not infer it from majority voting alone.

**Proof:** impersonation/replay/revocation/partition tests, key extraction resistance,
membership audit and formal fault-model review.

**Owner:** Suyash. **Target:** L5.

## 5. Security architecture

### 5.1 Secure boot and measured software

**Current gap:** OP-TEE signs receipts but does not establish boot, model-loader or estimator
integrity.

**Add:** secure/measured boot, signed OS/application/model bundles, anti-rollback counters,
device attestation covering versions/configuration and update recovery.

**Proof:** unsigned/old image refusal, measured-boot evidence, rollback attempt and recovery
exercise.

**Owner:** Suyash + platform owner. **Target:** L5.

### 5.2 Protected inference path

**Current gap:** frames, preprocessing, model loading and outputs remain outside OP-TEE.

**Add:** least-privilege inference service, immutable model mapping, input/output binding,
runtime measurement, memory isolation where platform permits and receipt fields binding
frame, preprocessing, model, output and command.

**Proof:** model/path substitution, TOCTOU, output tampering, process injection and crash
recovery tests.

**Owner:** Suyash + Samik. **Target:** L5.

### 5.3 Network and API security

**Current gap:** isolated demo LAN and scoped firewall rules are not a deployed security
architecture.

**Add:** mutual authentication/encryption, least-privilege ports, key rotation, rate limits,
message size/deadline bounds, replay protection, secure provisioning and audit logging.

**Proof:** unauthorized client, MITM, replay, flood, malformed payload, expired certificate
and recovery tests.

**Owner:** Suyash. **Target:** L5.

### 5.4 Dashboard/operator security

**Current gap:** demo token/tunnel is not operational access control.

**Add:** role-based access, strong operator identity, separate observe/command permissions,
two-person approval for dangerous actions, immutable audit, session expiry and local
offline mode.

**Proof:** privilege/CSRF/session/replay tests, audit completeness and loss-of-dashboard
operation.

**Owner:** Suyash + Abhijan. **Target:** L5.

### 5.5 Data protection

**Current gap:** survivor imagery/location retention policy is undefined.

**Add:** encryption at rest/in transit, minimum collection, access logging, configurable
retention/deletion, redaction/export policy and incident-response procedure.

**Proof:** access-control tests, lost-device exercise, deletion verification and agency
policy acceptance.

**Owner:** Suyash. **Target:** L5.

## 6. Command center and responder workflow

### 6.1 Geospatial dashboard

**Current gap:** model-hash dashboard lacks rescue mapping.

**Add:** offline base map, coverage layers, uncertainty ellipses, raw-evidence drill-down,
hazard/access layers, time controls, stale-data warnings and accessibility-aware alerting.

**Proof:** event-log replay produces identical state, no phantom markers, operator usability
test and degraded-link behavior.

**Owner:** Suyash + Abhijan. **Target:** L4/L5.

### 6.2 Alert prioritization

**Current gap:** no validated responder prioritization policy.

**Add:** transparent rule engine, configurable agency policy, evidence/uncertainty, duplicate
suppression, acknowledgement/escalation and mandatory human confirmation.

**Proof:** scenario-based responder review, no silent alert loss, deterministic replay and
false-priority analysis.

**Owner:** Suyash + emergency-domain adviser. **Target:** L5.

### 6.3 Situational reporting

**Current gap:** no standard report/export workflow.

**Add:** versioned machine-readable and printable report, GIS export, evidence references,
unsearched areas, unresolved alerts, vehicle/sensor limitations and handover log.

**Proof:** report-to-event consistency, schema validation, offline generation and responder
acceptance.

**Owner:** Suyash. **Target:** L5.

## 7. Simulation and verification

### 7.1 Disaster world library

**Current gap:** one FactoryCity scene cannot represent Indian disaster diversity.

**Add:** versioned flood, earthquake/rubble, landslide, cyclone and low-visibility scenarios;
randomized target/hazard placement; documented asset licenses; deterministic seeds; and
separate clean/attack worlds.

**Proof:** cold-reset reproducibility, actor/transform hashes, clearance tests and no truth
leakage.

**Owner:** Abhijan design; Pratik implementation. **Target:** L2/L3.

### 7.2 Sensor fidelity

**Current gap:** ideal simulator sensors overstate reliability.

**Add:** calibrated noise, latency, dropout, blur, lighting/weather, depth artifacts, GNSS
jamming/spoofing and thermal response tied to measured real sensors.

**Proof:** real-versus-sim distributions, parameter manifests and sensitivity analysis.

**Owner:** Pratik + Samik. **Target:** L3.

### 7.3 Monte Carlo campaigns

**Current gap:** two cold runs prove reproducibility, not statistical robustness.

**Add:** automated seeded campaigns across target positions, weather, failures, network,
sensor faults and model versions; aggregate success, safety and latency confidence bounds.

**Proof:** campaign manifest, complete results, no cherry-picking and regression thresholds.

**Owner:** independent verification lane. **Target:** L3/L4.

### 7.4 SIL, HIL and controlled flight

**Current gap:** SimpleFlight simulation is not PX4/HIL or real flight.

**Add:** PX4 SITL, autopilot failsafes, HIL timing, actuator limits, companion-computer link,
bench tests, tethered/contained flight and staged outdoor envelopes.

**Proof:** each gate's safety checklist, independent observer, logs, abort performance and
post-run inspection.

**Owner:** Pratik + flight-safety lead. **Target:** L4/L5.

## 8. Hardware/platform

### 8.1 Payload and compute

**Current gap:** current laptop/Jetson demonstration is not an integrated aircraft payload.

**Add:** selected RGB/thermal/depth/LiDAR/GNSS/IMU hardware, calibrated mounts, compute,
storage, power regulation, thermal management, EMC/vibration considerations and payload
mass/center-of-gravity budget.

**Proof:** power/thermal soak, vibration/calibration retention, latency under full load,
flight-time impact and safe shutdown.

**Owner:** hardware lead + Samik/Pratik. **Target:** L4.

### 8.2 Qualcomm deployment

**Current gap:** ONNX readiness is not Snapdragon/QNN execution.

**Add:** named Snapdragon target, representative calibration data, Qualcomm AI Hub/QNN
compile, output-parity tests, NPU profiling, power/thermal measurement and fallback runtime.

**Proof:** device-executed accuracy/latency/power evidence and artifact/version hashes.

**Owner:** Samik + Qualcomm/platform liaison. **Target:** L4/L5.

### 8.3 Per-aircraft root of trust

**Current gap:** one Jetson key cannot represent a fleet.

**Add:** unique secure element/TEE-backed identity per aircraft, manufacturing/provisioning
record, certificate rotation/revocation and replacement procedure.

**Proof:** no shared private keys, cross-device impersonation refusal and lost-device
revocation drill.

**Owner:** Suyash + hardware lead. **Target:** L5.

## 9. Software engineering and operations

### 9.1 Reproducible builds and dependencies

**Add:** pinned lockfiles, container/build manifests where suitable, deterministic model
converters, SBOM, vulnerability/license scan and artifact signing.

**Proof:** clean-machine rebuild and byte/hash or documented numerical reproducibility.

**Owner:** all component owners; independent lane verifies. **Target:** L5.

### 9.2 Configuration and secrets

**Add:** strict versioned schemas, no operational defaults for safety limits, external
secret store, key separation, redacted diagnostics and configuration migration tests.

**Proof:** malformed/unknown field rejection, secret scan and rollback/migration exercise.

**Owner:** Suyash + Pratik. **Target:** L5.

### 9.3 Observability and evidence

**Add:** structured metrics/logs/traces, synchronized clocks, append-only evidence chain,
health dashboards, storage bounds and post-mission export verifier.

**Proof:** failure root cause from retained evidence, chain verification and full-state
replay.

**Owner:** Suyash. **Target:** L5.

### 9.4 Recovery and maintenance

**Add:** startup preflight, watchdogs, process supervision, crash-only restart where safe,
dual-bank updates, rollback, backup/restore and field replacement runbooks.

**Proof:** injected process/power/storage failures and timed recovery drills.

**Owner:** Suyash + Pratik. **Target:** L5.

## 10. Safety, human factors and deployment governance

### 10.1 Safety case

**Current gap:** tests are not a deployment safety argument.

**Add:** system hazards, severity/likelihood, safety requirements, traceability from hazard
to mitigation/test, residual-risk acceptance and independent review.

**Proof:** maintained safety case for the exact aircraft, payload, software and operation.

**Owner:** Suyash + qualified safety lead. **Target:** L5.

### 10.2 Human-in-the-loop

**Add:** operator roles, confirmation points, alert acknowledgement, manual override,
authority transfer, workload limits, training and clear degraded-mode indicators.

**Proof:** realistic responder exercises, usability/error studies and training records.

**Owner:** Suyash + Abhijan + rescue-agency adviser. **Target:** L5.

### 10.3 Regulatory and operational approval

**Add:** verify current aviation, spectrum, privacy, mapping, disaster-response and local
operating requirements for the deployment jurisdiction; define airspace authorization,
insurance, maintenance, incident reporting and data policy.

**Proof:** written approval/checklist for the actual jurisdiction and mission. Do not copy
generic legal claims into the product.

**Owner:** project lead with qualified legal/regulatory advisers. **Target:** L5.

## 11. Recommended completion order after the 36-hour sprint

1. Close mission-controller separation, simulator adapter and evidence pipeline.
2. Close RGB person detection, tracking, geolocation and responder map.
3. Close depth/LiDAR mapping, global/local planning and collision avoidance.
4. Close task allocation, offline synchronization and inter-drone separation.
5. Add real thermal hardware/model and calibrated RGB–thermal fusion.
6. Add flood/access and fire/smoke specialists one at a time with held-out evidence.
7. Add GPS-denied VIO/SLAM and estimator integrity.
8. Add per-aircraft roots of trust, measured boot and protected model/runtime path.
9. Move through SIL, HIL, contained flight and controlled outdoor campaigns.
10. Complete deployment-specific safety, regulatory and responder acceptance.

Do not advance a capability to the next closure level because the demo looks convincing.
Advance it only when the evidence named above passes and is independently reviewed.

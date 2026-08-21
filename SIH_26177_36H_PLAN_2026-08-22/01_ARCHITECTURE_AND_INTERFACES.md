# Architecture and interface freeze

## 1. End-to-end data flow

```text
mission polygon
   -> sector allocator -> per-drone coverage waypoints
   -> waypoint controller ------------------------------+
                                                           |
RGB / synthetic-thermal -> specialist inference -> tracks  |
depth or LiDAR ----------> obstacle map / safety gate ------+-> command adapter -> CoSys
pose + calibration ------> observation geolocation          |
                                                           |
model identity -> receipt -> peer policy -> permit/HOLD -----+

tracks + hazards + coverage + vehicle health
   -> append-only mission events -> alert engine -> dashboard -> situational report
```

Semantic detection and metric obstacle avoidance are separate. A person box, fire box or
flood mask is an observation, not a velocity command. Depth/LiDAR and the route planner
decide whether the commanded corridor is traversable.

## 2. Required modules

### `rescue/ontology.py`

Canonical labels and capability declaration. Initial labels:

```text
person_candidate
water_or_flood
road_blocked
debris
fire
smoke
structure_damage
```

Only labels present in a model registry entry may be emitted as model detections. A model
may support a strict subset. `chemical_leak`, `exposed_electrical_line` and structural
instability are not accepted P0 vision classes; they require dedicated sensing/training.

### `rescue/perception_adapter.py`

Inputs: source ID, frame ID, capture time, image, model registry entry.
Output: normalized observations; no control vector.

### `rescue/tracker.py`

Maintains short-lived source-local tracks. A lightweight Kalman filter plus ByteTrack-style
association is adequate. It must expose track age, misses and source; no extra weights are
required.

### `rescue/geolocate.py`

Projects a box bottom-center or mask point using camera calibration, drone pose and metric
depth/range. Output uncertainty must grow with pose/depth uncertainty. If range or pose is
missing/stale, emit an image observation without a map point; never invent coordinates.

### `rescue/map_store.py`

Stores coverage cells, obstacle cells, hazards and candidate people. Deduplicate compatible
observations within a frozen spatial/temporal gate while retaining all source evidence.

### `rescue/search_planner.py`

Clips a lawnmower pattern to each assigned polygon. The same input manifest must produce
identical waypoint bytes. Search paths must respect geofence, altitude and separation.

### `rescue/local_planner.py`

Consumes route target and metric occupancy. Minimum accepted behavior is stopping-distance
HOLD. Preferred behavior is bounded 2-D A* around an occupied cell followed by rejoining
the coverage path. Climb-over is allowed only when upward clearance and altitude limits are
both confirmed.

### `rescue/task_allocator.py`

Partitions the search grid and records ownership. On a verified drone failure/quarantine,
unvisited cells are reassigned deterministically; visited cells and alerts are not erased.

### `rescue/alert_engine.py`

Rules must be readable and deterministic. Suggested priorities:

- P1: person candidate within/adjacent to a severe hazard;
- P2: person candidate with multi-sensor or repeated-track confirmation;
- P3: single-source person candidate needing confirmation;
- H1: route blocked, fire/smoke or deep-water zone affecting responder access.

The engine recommends review/dispatch priority; it does not diagnose injury or confirm
survival.

### `rescue/report.py`

Creates JSON plus human-readable Markdown/HTML: mission ID, coverage, vehicle states,
person candidates, hazards, coordinates/uncertainty, evidence IDs, model identities,
unresolved observations and recommended responder review order.

## 3. Frozen event envelope

Every event is JSON and contains:

```json
{
  "schema": "veriswarm.rescue.event.v1",
  "event_id": "create-once-uuid",
  "mission_id": "OP-VARUNA-001",
  "sequence": 1,
  "event_time_ns": 0,
  "monotonic_time_ns": 0,
  "node_id": "alpha",
  "event_type": "observation",
  "payload": {},
  "evidence": {
    "frame_sha256": null,
    "model_sha256": null,
    "config_sha256": null,
    "receipt_id": null
  }
}
```

Required event types:

```text
mission_started, vehicle_state, sector_assigned, waypoint_reached,
coverage_updated, observation, track_updated, map_marker, hazard,
alert_created, safety_hold, route_replanned, model_rejected,
sector_reassigned, communication_state, mission_completed, mission_aborted
```

Events are append-only. Sequence is monotonic per node. Reconnect sync is idempotent on
`event_id`. Dashboard state is a projection of events, not an independent source of truth.

## 4. Observation payload

```json
{
  "observation_id": "alpha-cam0-000123-person-0",
  "sensor": "front_rgb",
  "modality": "rgb",
  "capture_time_ns": 0,
  "frame_id": "alpha-000123",
  "label": "person_candidate",
  "confidence": 0.83,
  "bbox_xyxy_normalized": [0.1, 0.2, 0.3, 0.7],
  "track_id": "alpha-person-7",
  "world_point_ned_m": null,
  "horizontal_uncertainty_m": null,
  "model_id": "sar-rgb-person-v1",
  "synthetic_input": false
}
```

Thermal observations use `modality: "thermal"`. Cosys-generated infrared must also set
`synthetic_input: true` and identify the thermal-generation manifest.

## 5. Command contract

The command adapter accepts only a `ReleasedCommand` containing:

- mission and command IDs;
- vehicle ID;
- target pose/velocity in declared NED frame;
- TTL and issue time;
- geofence/altitude/separation checks;
- current occupancy/sensor freshness;
- planner reason;
- authorization/receipt reference; and
- explicit fallback action.

Any stale, malformed, unauthorized or frame-ambiguous command becomes HOLD. The adapter
must never accept a raw detector output.

## 6. Current-code changes required

The current `node/mission.py` uses the perception-derived action as the complete requested
command. For rescue, insert a waypoint controller and combine its route intent with the
metric local planner **before** `SafetySupervisor`; the supervisor gates the exact final
command sent to CoSys.

The current `perception/yolo_action.py` maps every detection into avoidance. Do not extend
that behavior to rescue labels. Introduce an observation-only adapter and leave the old
function intact for regression compatibility until callers migrate.

The current model-hash protocol remains a trust service. It may authorize an approved
model identity and reject an unapproved one, but it does not establish that a detection is
correct or that a flight path is safe.

## 7. Coordinate and timing rules

- Simulator world and flight commands use NED metres; Z is negative above the origin.
- Images retain pixel and normalized coordinates.
- Map markers must retain frame, pose, depth and calibration IDs.
- Use monotonic time for freshness/deadlines and wall-clock UTC only for reporting.
- Host receive time is not camera exposure time.
- Simulator truth belongs only to the evaluator and synthetic-label generator, never to a
  module presented as estimating pose, hazards or detections.

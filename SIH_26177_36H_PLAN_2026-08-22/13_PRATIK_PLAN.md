# Pratik — CoSys disaster world, autonomy and fleet integration plan

## Mission

Turn Abhijan's frozen scenario into a reproducible CoSys mission and replace the existing
straight, non-avoiding 70 m demonstration with a bounded search mission. You own simulator
implementation, sensor/pose transport, fleet control, coverage planning, obstacle safety,
task reassignment, landing and reset.

## Preserve first

- [ ] Keep the protected original FactoryCity project untouched.
- [ ] Preserve the current five-drone branch/evidence and uncommitted work.
- [ ] Split new work into two clean worktrees: disaster-world and rescue-autonomy.
- [ ] Do not use `simDestroyObject` on collided vehicles; it previously crashed Unreal.
- [ ] Resolve and document whether the new mission policy is `abort_all` or
      `isolate_failed_vehicle`; for this scenario use `isolate_failed_vehicle` only if the
      controller, config and narration all agree.

## H0–H5: derived world

- [ ] Receive `SCENARIO_MANIFEST.json` from Abhijan; reject missing coordinate values.
- [ ] Create a derived level/world ID such as `/Game/VeriSwarm/DisasterCorridor_26177`.
- [ ] Add stable actor names and collision meshes for people, rubble, flood and hazards.
- [ ] Configure exactly five vehicle IDs: alpha, bravo, charlie, delta, echo.
- [ ] Generate RGB, DepthPlanar and pose/state streams for every drone.
- [ ] Add instance segmentation labels where supported.
- [ ] Produce a read-only endpoint and actor/transform probe before flight.
- [ ] Prove two identical cold boots/resets and send the result plus branch/commit to
      Abhijan/Suyash. Continue work without waiting for approval.

## H3–H8: deterministic mission configuration

- [ ] Freeze launch positions, search polygon, geofence, altitude band, speed, acceleration,
      separation, sensor rates, TTLs and timeouts in validated configuration.
- [ ] Generate five non-overlapping sectors using a deterministic seed.
- [ ] Generate lawnmower waypoints clipped to each sector.
- [ ] Keep vehicle-local commands and world-NED points explicit; do not repeat the earlier
      global/local frame mix-up.
- [ ] Validate every waypoint against geofence/altitude before constructing a client.
- [ ] Render a path image and machine-readable route manifest.

H8 gate: one drone completes a search path, lands and resets without any detector or
dashboard dependency.

## H6–H14: sensor and perception bridge

- [ ] Implement a frame source that returns RGB, depth, pose and timestamps from the same
      declared vehicle and camera.
- [ ] Measure skew/freshness; do not call separately fetched values synchronized.
- [ ] Save frame IDs and paths so Samik's observation traces to simulator evidence. Hash
      only frames retained in the final demo bundle, automatically at packaging.
- [ ] Call Samik's inference adapter; never import dataset truth.
- [ ] Geolocate person/hazard observations using pose + calibration + metric depth.
- [ ] If depth is invalid/stale, emit image-only observation and no map point.
- [ ] Send events to Suyash's collector; buffer locally if unavailable.

H14 gate: one held-out simulator frame becomes one dashboard person-candidate marker with
frame/model/pose provenance and bounded uncertainty.

## H8–H18: obstacle-safe navigation

- [ ] Replace perception-box steering with route intent plus metric obstacle safety.
- [ ] Compute stopping distance using speed, maximum deceleration, latency and margin.
- [ ] Mark occupied cells from fresh depth/LiDAR observations.
- [ ] Minimum behavior: HOLD before entering an unsafe corridor.
- [ ] Preferred behavior: bounded 2-D A* around the occupied cell, then rejoin the coverage
      path. If no path exists, hold and mark the cell/sector blocked.
- [ ] Climb only when upward clearance and altitude headroom are both confirmed.
- [ ] Monitor collision, geofence, separation, pose freshness, sensor freshness and command
      TTL continuously—not just at phase boundaries.
- [ ] Add tests for stale depth, impossible route, obstacle on path, no upward clearance,
      geofence edge and command timeout.

## H14–H24: five-drone mission manager

- [ ] Start with two drones; pass the end-to-end thin slice before enabling five.
- [ ] Run vehicle commands concurrently while retaining one owner/context per CoSys client.
- [ ] Track assigned, visited, blocked and unvisited cells.
- [ ] Avoid duplicate person alerts through map-level spatial/temporal deduplication.
- [ ] On one quarantined/unavailable drone, stop/land it safely and reassign only its
      unvisited cells deterministically.
- [ ] Healthy drones continue only if separation, geofence and sensor gates remain valid.
- [ ] Land, disarm, release API control and reset every vehicle with explicit verification.

## H20–H30: campaigns and freeze

- [ ] Clean mission: all healthy, person/hazard mapping and completion.
- [ ] Obstacle mission: safe HOLD/replan with zero collision.
- [ ] Model rejection: one drone quarantined, HOLD and sector reassignment.
- [ ] Event-link outage: local buffer then idempotent synchronization.
- [ ] Stale sensor: affected drone HOLDs; no guessed motion.
- [ ] Run focused and full tests; report all deselections/skips.
- [ ] Push clean commits with no Unreal/generated artifacts.
- [ ] Freeze the runnable external bundle at H30 with world, settings, commands and reset
      card; generate one bundle-level checksum manifest automatically.

## Required evidence per run

```text
branch/commit plus final release-manifest identity
raw startup and endpoint probes
per-vehicle pose/velocity/collision/separation telemetry
sensor freshness and command TTL decisions
coverage and task-assignment events
raw observations and geolocation inputs
safety HOLD/replan evidence
landing/disarm/API-release/reset verification
complete console, JSONL, summary and video
```

## Fallbacks

- Five-drone instability at H24: freeze the accepted two-drone mission.
- A* incomplete: keep stopping-distance HOLD and use a precomputed alternate waypoint only
  when the manifest proves it clear; say “bounded alternate route,” not online replanning.
- Synthetic thermal unstable: remove it from the live run; retain RGB + depth.
- Fire visual hurts performance: replace Niagara fluids with a lightweight sprite effect.
- Detector fails on mannequins: use a held-out synthetic adaptation set only if trained and
  evaluated honestly; otherwise demonstrate detection on retained real UAV frames beside
  the simulator and do not fabricate simulator detection.

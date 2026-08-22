# FactoryCity Disaster world lane

This directory owns Pratik's additive CoSys disaster-world tooling. It does not contain
the Unreal project, generated settings, raw frames, logs, or development evidence.

## Implemented contract

1. `unreal_duplicate_disaster_world.py` creates a separate world and never loads the
   duplicated `UWorld` in the same process. A fresh commandlet must inventory it.
2. `unreal_disaster_inventory.py` verifies Point_A, the AirSim origin, roads, floors,
   collision profiles, and tagged disaster actors without modifying the level.
3. `unreal_disaster_asset_inventory.py` discovers reusable project assets before the
   builder references them.
4. `unreal_apply_disaster_layer.py` consumes
   `factorycity_disaster.development.json`. The v2 development manifest replaces only the
   explicitly tagged prior scenario layer, fits one non-colliding flood surface to the full
   Landscape XY bounds, and distributes physical debris/roadblocks away from Point_A.
5. `build_disaster_settings.py` derives camera settings and placement metadata from the
   protected five-drone inputs and `factorycity_disaster_camera_profile.json`.
6. `verify_disaster_sensor_streams.py` captures RGB, DepthPlanar, and pose from every
   declared vehicle and fails on missing/invalid streams.
7. The PowerShell tools guard closed-editor settings/default-map mutations and preserve
   backups and operation records.
8. `run_rising_flood_swarm.py` resolves the water actor from the retained layer result,
   raises it through the supported CoSys scene-object API, reads the observed water pose,
   and commands all five drones to a configuration-controlled clearance above that pose.
   It verifies clearance, separation, collision state, roster, and safe recession before
   landing; rain, fog, and road wetness also come from configuration.
9. `unreal_point_b_audit.py` validates the saved Point_B, route distance, peak-flood
   clearance, marker rotation, and nine collision probes across the 5x5 m landing area.
10. `unreal_add_point_b_landing_collision.py` adds one invisible, bounded `BlockAll`
    collision surface when a visually solid source roof lacks usable landing collision.
11. `run_factorycity_ab_swarm.py` performs the nominal five-drone mission. It climbs to
    the configured 10 m NED cruise height, starts collision monitoring only after takeoff,
    advances every active drone along the same straight horizontal vector, dynamically
    increases water clearance if required, terminates newly collided drones, and lands
    surviving drones at the audited Point_B before disarm/API cleanup.
12. `factorycity_joint_movement_contract.development.json` freezes the exact NED
    endpoints, initial poses, route cells, limits, authorization effects, event surface,
    reset procedure and Abhijan security-test inputs without exposing hidden truth to the
    controller. See `JOINT_MOVEMENT_SECURITY_CONTRACT.md`.
13. `factorycity_sensor_movement_extension.v2.development.json` and `movement_v2.py`
    implement the additive post-freeze movement core. The v2 file is hash-bound to the
    accepted v1 movement contract and Suyash's cell-event extension. It derives exact
    assignment/coverage IDs from the loaded cells, reduces measured front depth into
    fail-closed left/centre/right/upper clearances, enforces a real hover before any
    measured left/right/up deflection, keeps blocked/collided states terminal, and
    durably publishes exact current-owner reassignment state. It invokes Abhijan's
    authorization/limit gate immediately before a supplied CoSys mutation. The accepted
    straight-route v1 file and evidence are unchanged; this core is not live CoSys proof.
14. `tools/run_factorycity_movement_v2.py` is the separate live movement-v2 runner. It
    connects `SensorDrivenMovementSupervisor`, `CellLedger`, `DurableMovementEvents`, and
    `GatedCommandDispatcher` to real CoSim depth/collision/vehicle reads and gated API,
    arm, takeoff, hover, velocity, deflection, rejoin, quarantine-land and Point_B descent
    calls. It never reads evaluator obstacle coordinates or survivor truth. Missing,
    malformed, stale or HOLD authorization fails closed; QUARANTINE cannot start a
    grounded vehicle and aborts an in-flight one. Producer sequences persist across
    process restarts in the per-source outboxes. Start it on Windows with
    `ops/start_pratik_movement_v2.ps1`.

## Live movement-v2 operating order

1. On Abhijan's Mac, run `./ops/start_abhijan_pratik_mac.sh`.
2. On Pratik's Windows PC, run `ops/start_pratik_rescue_sender.ps1` in one terminal.
3. Keep an authorization producer atomically refreshing the configured
   `authorization_snapshot.json` with one canonical, current event for every drone. A
   static file becomes stale after the frozen two-second lease and therefore holds the
   mission; the runner never manufactures `ALLOW`.
4. Open `FactoryCity_Disaster`, press Play, and run
   `ops/start_pratik_movement_v2.ps1` in a second Windows terminal.
5. Retain JSON, SQLite outboxes, captures and video outside Git. The event sender drains
   the exact per-producer SQLite files to Abhijan's restricted Ethernet ingress.

The committed Ethernet handoff is currently Windows-to-Mac for rescue events. A reviewed
Mac-to-Windows authorization refresher is still required for a real multi-minute ALLOW /
HOLD / QUARANTINE demonstration; the local file boundary is deliberately fail-closed
until that integration exists.

The development weather profile prioritizes demo visibility: light rain and wet roads
remain enabled, while fog, dust, snow, and airborne-leaf effects are explicitly zeroed.
At peak flood, the controller remains active and repeatedly holds all five drones above
the observed water pose. The operator presses Ctrl+C to trigger configured recession,
landing, disarming, weather cleanup, and API-control release.

## Collision policy

- Existing road and launch-ground actors remain untouched.
- Physical roadblocks, debris, and damaged-structure props use `BlockAll` with
  `QueryAndPhysics`.
- The map-wide flood plane is a movable visual overlay with `NoCollision`; it does not create a false solid
  ceiling over a road.
- Every added actor is tagged `VS_Disaster`, scenario ID, kind, and development/frozen
  status.

## Current development scenario

The development file uses audited existing road actor labels as anchors. Its values are
configuration data, not controller constants. It is intentionally marked
`development_only: true` because Abhijan's final `SCENARIO_MANIFEST.json` has not yet been
published. Do not present these provisional locations as evaluator truth.

The Unreal project path is supplied by the operator and is intentionally absent from
portable Git configuration. Development evidence belongs under the owner's external
`scratch/<owner>/<task>/latest/` area and may be regenerated.

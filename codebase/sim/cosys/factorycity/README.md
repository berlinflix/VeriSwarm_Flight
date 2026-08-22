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

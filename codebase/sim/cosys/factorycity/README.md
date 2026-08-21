# FactoryCity five-drone workstream

This directory is the portable, reviewable control surface for Pratik's FactoryCity
five-drone work. The Unreal project, packaged builds, raw captures, and high-volume
evidence remain external content-addressed artifacts.

## Branch and base

- Feature branch: `codex/pratik-five-drone-factorycity`
- Base: `origin/main` at `4c83e7b8550dda407acb53705a29aec088a3c9d6`
- Phase 0 status: frozen and verified
- Phase 1 status: configuration contract implemented and promoted by Phase 3 evidence
- Phase 2 status: deterministic placement and artifact rendering implemented
- Phase 3 status: endpoint-A FactoryCity/CoSys integration accepted and live-verified

## Scope

Pratik owns the FactoryCity/CoSys integration and the collective fleet controller,
including RPC ownership, arming, bounded commands, collision and separation monitoring,
abort, landing, cleanup, and the adapter from authorized VeriSwarm intents.

The accepted scenario will configure five vehicles and a 10 m by 10 m launch area. Values
such as vehicle identities, fleet size, launch-area dimensions, launch coordinates, RPC
endpoint, limits, timeouts, ordering, and failure policy belong in validated configuration.
Controller code must not embed them.

Concrete spawn coordinates are generated deterministically from configuration and live
scene-clearance checks. The generator must reject an area that cannot safely contain the
configured fleet.

## Phase order

1. Repository and recovery baseline.
2. Contracts and configuration system.
3. Deterministic launch-position generation.
4. FactoryCity and CoSys-AirSim integration.
5. Read-only fleet RPC adapter.
6. Generic one-drone lifecycle.
7. Five-drone spawn qualification.
8. Collective lifecycle baseline.
9. Collective swarm-control interface.
10. Runtime safety, abort, landing, and cleanup.
11. VeriSwarm authorization bridge.
12. Fault and acceptance campaigns.
13. Pull request, release, and teammate reproduction.

Every phase has an explicit exit gate. A later phase must not be described as complete
until the earlier gate passes with retained evidence.

## Repository contents

- `phase0_baseline.json` freezes the source/runtime/repository identities observed before
  integration.
- `world_manifest.json` records the accepted portable FactoryCity/runtime identities;
  `world_manifest.template.json` remains the blank reusable form.
- `factorycity_fleet_config.schema.json` is the portable Draft 2020-12 structural schema.
- `config.py` is the authoritative strict parser for structural and cross-section safety
  invariants. It performs no RPC calls and supplies no operational defaults.
- `factorycity_five_drone.template.json` is the accepted five-vehicle endpoint-A contract.
  Operational values remain data in this document rather than constants in Python.
- `phase3_exit_audit.json` identifies the authoritative scene, settings, live-RPC, test,
  and local direct-check evidence without storing machine-specific paths.

## Mission boundary after Phase 3

Endpoint A is accepted. Endpoint B and the appropriate A-to-B distance are configuration
work for the flight phase. Ground contact at A or B is permitted when takeoff or landing is
safe. Any obstacle collision during the airborne A-to-B segment marks the mission failed.
No API control, arming, flight, abort, or landing command was issued in Phase 3.
- `launch.py` calculates usable square bounds, derives a candidate lattice from configured
  fleet size and separation, calls a scene-clearance provider for every candidate, performs
  seeded max-min selection, revalidates the resulting plan, and renders deterministic CoSys
  settings and launch-manifest bytes.
- `examples/` contains a prominently labelled synthetic renderer example, never live scene
  evidence.

## Loading a contract

Call `load_config(path)` before constructing any simulator client. It returns an immutable
`FactoryCityConfig` plus the SHA-256 of the exact source bytes. Unknown fields, incomplete
rosters, unsafe ranges, inconsistent limits, missing sensors, non-portable paths, and
cross-section mismatches raise `ConfigurationError` before RPC creation.

The accepted scenario values belong in a reviewed configuration derived from the template.
Controller and adapter modules consume the validated object; they must not copy roster,
geometry, endpoint, limit, timeout, order, or policy values into source code.

## Phase 2 placement boundary

`generate_launch_plan(config, clearance_provider)` works for configured fleet size `N`.
The provider identity must match configuration and must return explicit ground-support,
vertical-corridor, ground-height, reason, and evidence values. Missing, malformed, blocked,
or inconsistent results fail closed.

`render_cosys_settings(config, plan, base_settings)` independently revalidates the complete
plan and patches only the exact configured roster's `VehicleType` and `X/Y/Z` values. All
other CoSys values must be present in the caller-supplied base settings document; the
renderer provides no endpoint, orientation, sensor, or runtime defaults.

`render_launch_manifest(...)` records every clearance evaluation, generator input, selected
position, pairwise distance, safety check, the exact configuration hash, and the generated
settings hash. It contains no timestamp, so identical inputs produce identical bytes.
`write_create_once(...)` persists those bytes with exclusive creation and rejects any
attempt to replace existing evidence.

## Never commit here

- Unreal `Content`, `DerivedDataCache`, `Intermediate`, `Saved`, or packaged binaries;
- Marketplace/Fab assets without confirmed redistribution rights;
- raw frames, videos, ZIP archives, or full-rate telemetry;
- local absolute paths, local network overrides, credentials, or private keys;
- simulator truth or expected outcomes used by the autonomy process.

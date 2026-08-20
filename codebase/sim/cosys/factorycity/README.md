# FactoryCity five-drone workstream

This directory is the portable, reviewable control surface for Pratik's FactoryCity
five-drone work. The Unreal project, packaged builds, raw captures, and high-volume
evidence remain external content-addressed artifacts.

## Branch and base

- Feature branch: `codex/pratik-five-drone-factorycity`
- Base: `origin/main` at `4c83e7b8550dda407acb53705a29aec088a3c9d6`
- Phase 0 status: frozen and verified
- Phase 1 status: configuration contract implemented; live scene acceptance remains pending

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
- `world_manifest.template.json` is a portable placeholder for the derived FactoryCity
  environment manifest. Phase 3 fills measured values and hashes.
- `factorycity_fleet_config.schema.json` is the portable Draft 2020-12 structural schema.
- `config.py` is the authoritative strict parser for structural and cross-section safety
  invariants. It performs no RPC calls and supplies no operational defaults.
- `factorycity_five_drone.template.json` declares the intended five-vehicle, 10 m by 10 m
  contract. Its `TEMPLATE_NOT_SCENE_VALIDATED` status is intentional; Phase 3 scene
  qualification is required before promotion to `ACCEPTED`.

## Loading a contract

Call `load_config(path)` before constructing any simulator client. It returns an immutable
`FactoryCityConfig` plus the SHA-256 of the exact source bytes. Unknown fields, incomplete
rosters, unsafe ranges, inconsistent limits, missing sensors, non-portable paths, and
cross-section mismatches raise `ConfigurationError` before RPC creation.

The accepted scenario values belong in a reviewed configuration derived from the template.
Controller and adapter modules consume the validated object; they must not copy roster,
geometry, endpoint, limit, timeout, order, or policy values into source code.

## Never commit here

- Unreal `Content`, `DerivedDataCache`, `Intermediate`, `Saved`, or packaged binaries;
- Marketplace/Fab assets without confirmed redistribution rights;
- raw frames, videos, ZIP archives, or full-rate telemetry;
- local absolute paths, local network overrides, credentials, or private keys;
- simulator truth or expected outcomes used by the autonomy process.

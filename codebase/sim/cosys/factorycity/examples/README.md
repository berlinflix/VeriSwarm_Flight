# Phase 2 synthetic output example

This directory demonstrates the Phase 2 renderer only. It is **not FactoryCity scene
evidence**, is not an accepted `settings.json`, and must not be used to claim that any
vehicle has spawned or flown.

`phase2_synthetic_settings.example.json` was produced for the template configuration with
an explicitly synthetic flat-clearance provider. The provider reported
`scene_validated = false`; all ground heights and orientations in this example are test
data. The example shows that configured vehicle records are preserved while only
`VehicleType` and generated `X`, `Y`, and `Z` fields are applied.

For the template seed, the pure max-min generator selected:

| Vehicle | Candidate | X (m) | Y (m) | Synthetic Z NED (m) |
|---|---:|---:|---:|---:|
| alpha | r5c0 | -4.5 | 4.5 | 0.0 |
| bravo | r0c5 | 4.5 | -4.5 | 0.0 |
| charlie | r5c5 | 4.5 | 4.5 | 0.0 |
| delta | r0c0 | -4.5 | -4.5 | 0.0 |
| echo | r2c2 | -0.8999999999999999 | -0.8999999999999999 | 0.0 |

The minimum pairwise distance is `5.091168824543142 m`, above the configured `2.0 m`.
The usable square is `9 m × 9 m` after applying the configured edge clearance to the
declared `10 m × 10 m` area.

The full deterministic launch manifest, including all candidate clearance decisions,
input and settings hashes, positions, pairwise distances, and pass/fail checks, is rendered
and independently hash-verified by `test_factorycity_launch.py`. Phase 3 replaces the
synthetic provider with live FactoryCity clearance evidence before any result can be
promoted to scene-validated status.

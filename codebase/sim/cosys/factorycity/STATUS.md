# Pratik C4 world-lane status

Owner: Pratik

Branch: `pratik/sih26177-disaster-cosys`

Plan base: `e3f9559add11f4815bafac43b29a953e0eba742f`

## Owned Git surface

- `codebase/sim/cosys/factorycity/**`
- `codebase/tests/test_factorycity_*.py`

## External owned development surface

- Derived CoSys/Unreal disaster project and map created from the protected FactoryCity
  baseline. Unreal projects, generated settings, captures, logs and development evidence
  stay outside Git.
- Overwriteable development output uses
  `scratch/pratik/factorycity-disaster/latest/` outside this worktree.

## Read-only inputs owned by other lanes

- Abhijan's scenario manifest, truth actors and expected-event contract.
- Samik's inference/model registry contract.
- Suyash's rescue event schema and collector interface.

This lane will not change a shared schema incompatibly or edit another owner's files
without the coordination required by `05_FAST_INDEPENDENT_EXECUTION_RULES.md`.

## Current milestone

Create a derived disaster world without modifying the protected FactoryCity baseline;
preserve the verified five-drone Point-A spawn; add manifest-driven actors, collisions,
RGB/depth/pose capture and repeatable reset/probe commands.

## Current limitation

`SCENARIO_MANIFEST.json` has not been handed off. World-copy/tooling work may proceed, but
final victim, flood, debris, blocked-road and hazard placement must not be guessed or
hard-coded.

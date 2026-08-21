# VeriSwarm Rescue — SIH 26177 36-hour execution package

**Created:** 22 August 2026
**Problem statement:** 26177 — deployable AI-powered autonomous drone for search and rescue
**Status:** execution plan; it does not alter or supersede preserved qualification evidence

## Product sentence

VeriSwarm Rescue is an offline-first multi-drone search system that partitions a disaster
area, follows bounded search paths, detects **person candidates** and selected hazards on
edge hardware, geolocates and deduplicates observations, and presents responder-ready
alerts while the existing VeriSwarm trust layer rejects unapproved perception models.

Do not call a detected person a confirmed survivor. The software produces a person
candidate for responder confirmation. Do not claim detection of a hazard class unless a
held-out evaluation and the accepted demo run actually measure that class.

## The one 36-hour scenario

**Operation Varuna — Flooded Urban Search**

Five simulated drones launch from a safe staging area and receive non-overlapping search
sectors. They execute deterministic lawnmower coverage. The mission contains visible
person targets, floodwater, blocked access routes, debris and one visually marked fire or
smoke zone. RGB/thermal person observations and accepted hazard observations become map
markers and prioritized alerts. Metric depth/LiDAR, not semantic boxes, protects the
flight path. One drone can be denied authorization by the existing model-hash trust layer;
its unvisited cells are reassigned and the remaining fleet continues.

## Mandatory reading order

1. `00_COMBINED_36H_EXECUTION_PLAN.md`
2. `01_ARCHITECTURE_AND_INTERFACES.md`
3. `02_DATASET_MODEL_AND_EDGE_PLAN.md`
4. `03_KAGGLE_DATASET_SHORTLIST.md`
5. `04_PRESERVED_VERISWARM_CAPABILITIES.md`
6. Your individual file:
   - `10_SUYASH_PLAN.md`
   - `11_SAMIK_PLAN.md`
   - `12_ABHIJAN_PLAN.md`
   - `13_PRATIK_PLAN.md`
   - `14_AYUSH_PLAN.md`
7. `20_GAPS_RISKS_AND_REMEDIES.md`
8. `21_ACCEPTANCE_MATRIX_AND_DEMO.md`
9. `22_FULL_REMEDIATION_BACKLOG.md` for the complete post-sprint engineering roadmap
10. `30_START_NOW.md`

## Non-negotiable boundaries

- Preserve all previous Q-B, camera, OP-TEE and protocol evidence unchanged.
- Preserve the five-camera and attack demonstrations as optional, independently accepted
  VeriSwarm capabilities; do not rewrite their evidence to fit the rescue mission.
- Work in clean worktrees and dedicated branches. The dirty
  `C:\Users\suyas\sih` checkout must not be pulled, reset or used as a merge workspace.
- Datasets, weights, virtual environments, videos, Unreal projects and evidence stay out
  of Git. Commit only manifests, hashes, converters, configuration, code, tests and docs.
- Seven Codex sessions are seven parallel engineering lanes, not permission for seven
  sessions to edit the same files.
- Feature work stops at hour 30. Hours 30–36 are exclusively for integration, two
  unchanged rehearsals, evidence review, pitch preparation and fallback verification.

## What the 36-hour build will and will not prove

It can prove a coherent simulator-based thin slice: autonomous sector coverage, measured
edge inference, observation geolocation, hazard/person mapping, alert generation,
offline operation and model-provenance rejection.

It will not prove certified rescue use, reliable recognition of every listed hazard,
full GPS-denied autonomy, real thermal-camera performance, real-world collision safety,
Qualcomm NPU execution, or military/aviation readiness. Those remain explicit roadmap
items unless separately implemented and measured.

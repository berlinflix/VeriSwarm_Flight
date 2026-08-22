# STATUS — Pratik

```text
owner: Pratik
branch: pratik/sih26177-disaster-cosys
last_suyash_inbox_commit_seen: d0bd417
last_abhijan_peer_commit_consumed: 9d1f03dfe5478a14535431810df88a1c061afef9
current_nominal_controller_commit: 17c27f3
current_live_movement_v2_commit: b32f562
current_integration_merge: 63a1480
completed: Live movement-v2 runner plus Abhijan's restricted Mac-to-Windows five-lease receiver are integrated; nominal-v1 remains unchanged
next: Retain the live nominal ALLOW, transient single-vehicle HOLD and terminal QUARANTINE FactoryCity runs, then add the Point_B survivor and bounded flood debris visuals
blockers: NONE for code; both operators and the isolated Ethernet link are required for retained live qualification
```

Checkpoint tag:
`checkpoint/pratik-factorycity-flood-persistent-peak-v1`

The portable branch is clean and the external Unreal rollback bundle is retained outside
Git. Raw Unreal assets, generated captures and runtime evidence remain outside Git.

Joint contract handoff:

- contract commit: `bc5cbd7`
- nominal controller commit: `d96cf1c`
- focused tests: `18 passed`
- full branch tests: `379 passed, 3 skipped`

## Live nominal A-to-B milestone — PASS

Development evidence remains outside Git at
`scratch/pratik/factorycity-disaster/latest/ab_mission_run.json`.

- map: `/Game/VeriSwarm/FactoryCity_Disaster`
- roster: `alpha`, `bravo`, `charlie`, `delta`, `echo`
- route: one straight 95.0474 m Point_A-to-Point_B line
- evidence status: `PASS`
- en-route control samples: `96`
- maximum arrival error: `0.8262 m` (`1.0 m` limit)
- maximum cross-track error: `0.0066 m` (`1.0 m` limit)
- minimum observed separation: `2.7421 m` (`1.5 m` limit)
- final rooftop separation: `2.8284 m`
- final state: all five `Landed`, zero vertical speed, correct audited rooftop collider
- post-merge FactoryCity/movement/security/replay tests: `58 passed`

Run command:

```text
VeriSwarm_FactoryCity_UE5.8.1_Phase3/Run_Five_Drone_A_to_B.cmd
```

The result qualifies the accepted nominal-v1 live path only. It is not evidence of live
sensor-driven obstacle deflection, cell reassignment, authorization HOLD/QUARANTINE or
collision termination; those remain the next additive movement-v2 acceptance runs.

## Additive live movement-v2 runner — implemented, live evidence pending

Code milestone: `b32f562`

- preserved `run_factorycity_ab_swarm.py` and its nominal-v1 evidence unchanged;
- connected `SensorDrivenMovementSupervisor`, `CellLedger`,
  `DurableMovementEvents`, and `GatedCommandDispatcher` to actual CoSim calls;
- gates API control, arm, takeoff, route/deflection/rejoin, HOLD hover,
  QUARANTINE hover-land and Point_B descent at command boundaries;
- reloads authorization between QUARANTINE hover and landing;
- consumes only measured depth, collision, vehicle state and immutable contracts;
- publishes ABORTED after an exceptional exit from an announced mission;
- persists per-source sequence counters so a restarted process cannot reuse event IDs;
- keeps one SQLite outbox per exact producer for Abhijan's direct Ethernet sender.

Run command:

```powershell
powershell -ExecutionPolicy Bypass -File .\ops\start_pratik_movement_v2.ps1
```

Focused regression:

```text
82 passed
```

Full Windows repository regression:

```text
558 passed, 3 skipped, 1 pre-existing POSIX owner-mode assertion failed on Windows
```

No movement-v2 live PASS is claimed yet. The reviewed authorization receiver is now
integrated at `0b2e7ee`; the retained ALLOW, HOLD and QUARANTINE runs remain to be executed
jointly over the isolated Ethernet link.

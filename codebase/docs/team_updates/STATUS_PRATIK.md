# STATUS — Pratik

```text
owner: Pratik
branch: pratik/sih26177-disaster-cosys
last_inbox_commit_seen: 57a62221dafd4d8b1207fc8b8d4a37ee9e87a11d
current_nominal_controller_commit: 17c27f3
current_integration_merge: 63a1480
completed: First retained live five-drone Point_A-to-Point_B mission passed, including rising flood, straight-route convergence, monitored rooftop descent, disarm, Landed confirmation and cleanup
next: Wire the reviewed movement-v2 supervisor, cell ledger, durable events and gated dispatcher into a new live CoSys runner without changing the accepted nominal-v1 runner
blockers: No code blocker; movement-v2 live scenario evidence and the original no-person CoSys RGB negative-control clip remain open
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
